# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import functools
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from provide.telemetry import get_logger

from octowright._tracing import counter, span
from octowright.browser_pool import close_helpers
from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.events import SessionCloseReason
from octowright.browser_pool.launch_helpers import rotate_har_path
from octowright.session.operation_gate import CloseReservation, OperationGateInvariantError, SessionClosedError

if TYPE_CHECKING:
    from octowright.browser_pool.pool import BrowserPool
    from octowright.session import BrowserSession

log = get_logger(__name__)

# Bumped once per coordinator run, regardless of trigger. Moved here from
# session/core_ops_mixin.py -- SessionOpsMixin.close() no longer runs for any
# production close (every real close routes through this module's
# coordinator instead), so this is now the only place a real close is ever
# observed to increment it.
_SESSION_CLOSED = counter(
    "octowright_browser_closed_total",
    description="Browser sessions closed cleanly via the pool's close coordinator",
)


@dataclass(slots=True)
class CloseCoordinatorOutcome:
    """The shared, once-resolved result of a durable close coordinator run."""

    response: dict[str, Any]
    prepared: object | None


@dataclass(slots=True)
class ClosingSession:
    """One entry in ``pool._closing_sessions``: a session mid-teardown plus
    the reservation/task that owns finishing it."""

    session: BrowserSession
    reservation: CloseReservation
    task: asyncio.Task[None] | None = None


def _protected_close_message(instance_id: str, reason: str) -> str:
    if reason == "headed_default":
        return (
            f"browser {instance_id!r} is headed/user-facing and protected by default "
            "(OCTOWRIGHT_PROTECT_HEADED). Pass force=True to close it, or relaunch with "
            "protected=False for scripted headed work."
        )
    return (
        f"browser {instance_id!r} is protected; pass force=True to close it. "
        "Protected browsers are meant to stay open for the user."
    )


def _resolve_close_target(pool: BrowserPool, instance_id: str, expected_session: Any | None) -> tuple[str, Any]:
    """Resolve an identity-aware close target while the caller holds the lock.

    ``expected_session`` (when given) is searched for by OBJECT identity
    across the whole registry, not looked up by ``instance_id`` -- the
    caller's id may be stale (a keep-id relaunch rekeys the object to a new
    id between when a caller captured it and when it calls close)."""
    if expected_session is None:
        session = pool._sessions.get(instance_id)
        if session is None:
            raise KeyError(pool._missing_session_message(instance_id))
        return instance_id, session
    current = next(
        ((current_id, candidate) for current_id, candidate in pool._sessions.items() if candidate is expected_session),
        None,
    )
    if current is None:
        raise KeyError(f"browser instance_id={instance_id!r} was evicted or rebound before close")
    return current


async def reserve_close_browser(
    pool: BrowserPool,
    instance_id: str,
    *,
    force: bool,
    reason: SessionCloseReason,
    expected_session: BrowserSession | None = None,
) -> ClosingSession:
    """Reserve the close cutoff and return its (possibly shared) coordinator entry.

    Holds ``pool._sessions_lock`` only for identity lookup, the existing-
    ``_closing_sessions`` coalescing check, registry insertion, and the short
    gate ``reserve_close`` control transaction -- never while awaiting a FIFO
    ticket, Playwright, artifact I/O, or the coordinator task.
    """
    async with pool._sessions_lock:
        try:
            resolved_id, session = _resolve_close_target(pool, instance_id, expected_session)
        except KeyError:
            # The object already left `_sessions` (its coordinator popped it
            # once the ticket owned the gate, or an external close raced
            # ahead of us) but may still be draining in `_closing_sessions`.
            existing = pool._closing_sessions.get(instance_id)
            if existing is not None and (expected_session is None or existing.session is expected_session):
                return existing
            raise
        existing = pool._closing_sessions.get(resolved_id)
        if existing is not None and existing.session is session:
            # A duplicate close for the SAME identity shares the one
            # coordinator already draining it, rather than starting a second.
            return existing

        def _preflight() -> None:
            if getattr(session, "protected", False) and not force:
                raise ProtectedBrowserCloseError(
                    _protected_close_message(resolved_id, getattr(session, "protected_reason", "explicit"))
                )

        reservation = await session._operation_gate.reserve_close("browser_close", preflight=_preflight)
        # gate.reserve_close's own admission lock can suspend THIS coroutine
        # for a full loop turn (contended by any other gated op on this
        # session) while we still hold pool._sessions_lock -- but the sync
        # external-close seam (accept_external_close_nowait) never takes
        # that lock, so it can win the race in that window: it marks the
        # gate closed and installs its own ClosingSession wrapping this
        # SAME reservation (reserve_close's `_close_reservation is not None`
        # short-circuit is what handed it back to us above). Re-check, after
        # the only await in this function, and share that entry rather than
        # wrapping the identical reservation in a second one -- else two
        # coordinators both run the full teardown.
        existing = pool._closing_sessions.get(resolved_id)
        if existing is not None:
            if existing.session is not session or existing.reservation is not reservation:
                raise OperationGateInvariantError(
                    f"session {resolved_id!r} close reservation raced an unrelated closing-registry entry"
                )
            return existing
        entry = ClosingSession(session=session, reservation=reservation)
        pool._closing_sessions[resolved_id] = entry
    _spawn_close_coordinator(pool, resolved_id, entry, reason=reason)
    return entry


async def close_browser(
    pool: BrowserPool,
    instance_id: str,
    *,
    force: bool = False,
    _reason: SessionCloseReason = "agent_close",
    _expected_session: BrowserSession | None = None,
) -> dict[str, Any]:
    entry = await reserve_close_browser(
        pool, instance_id, force=force, reason=_reason, expected_session=_expected_session
    )
    outcome = cast(CloseCoordinatorOutcome, await entry.reservation.wait())
    return outcome.response


def _spawn_close_coordinator(
    pool: BrowserPool,
    instance_id: str,
    entry: ClosingSession,
    *,
    reason: SessionCloseReason,
) -> None:
    """Create the detached, retained coordinator task and store it on ``entry``.

    Detached: the requester awaits only ``entry.reservation.wait()`` (itself
    shielded), so a requester's own cancellation never reaches this task.
    Retained: stored on ``entry.task`` so ``shutdown_pool`` can await it.
    """
    task = asyncio.create_task(_coordinate_close(pool, instance_id, entry, reason=reason))
    entry.task = task
    task.add_done_callback(functools.partial(_observe_close_coordinator, instance_id))


def _observe_close_coordinator(instance_id: str, task: asyncio.Task[None]) -> None:
    """Retrieve an unexpected exception from a detached coordinator task --
    its own try/except/finally always resolves the reservation and never
    re-raises, so reaching one here means something broke outside that
    contract; surface it rather than let asyncio log a bare unretrieved-
    exception warning."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        log.error("octowright.pool.close_coordinator_crashed", instance_id=instance_id, error=repr(exc))


async def _coordinate_close(
    pool: BrowserPool,
    instance_id: str,
    entry: ClosingSession,
    *,
    reason: SessionCloseReason,
    preparation: Any = None,
) -> None:
    """Run exactly once per ``entry``: admit under the gate (or take the bare
    teardown-only path), teardown, publish, and resolve the shared outcome.
    Owns the ``octowright.session.close`` span + closed-total counter -- the
    only place either fires now that ``SessionOpsMixin.close()`` no longer
    runs for a production close."""
    session = entry.session
    prepared: object | None = None
    error: BaseException | None = None
    response: dict[str, Any] | None = None
    recorder_reason = close_helpers.recorder_close_reason(reason)
    with span("octowright.session.close", instance_id=instance_id, kind=session.kind):
        try:
            try:
                async with session._operation_gate.close_operation(entry.reservation):
                    await close_helpers.remove_active_identity(pool, instance_id, session)
                    prepared, error = await close_helpers.prepare_then_teardown(session, preparation, recorder_reason)
            except SessionClosedError as exc:
                # An external browser/page close invalidated admission first.
                await close_helpers.remove_active_identity(pool, instance_id, session)
                prepared, teardown_error = await close_helpers.prepare_then_teardown(session, None, recorder_reason)
                if preparation is None:
                    error = teardown_error
                else:
                    error = exc
                    if teardown_error is not None:
                        close_helpers.log_secondary_teardown_error(session, teardown_error, primary=exc)
            response = close_helpers.close_response(session)
        except BaseException as exc:
            if error is None:
                error = exc
        finally:
            _SESSION_CLOSED.add(1, attributes={"kind": session.kind})
            await close_helpers.remove_manifest_best_effort(instance_id)
            close_helpers.publish_close_once(session, instance_id, reason)
            async with pool._sessions_lock:
                pool._sessions.pop(instance_id, None)
            if error is None:
                session._operation_gate.complete_close(
                    entry.reservation,
                    CloseCoordinatorOutcome(
                        response=response or close_helpers.close_response(session), prepared=prepared
                    ),
                )
            else:
                session._operation_gate.fail_close(entry.reservation, error)
            async with pool._sessions_lock:
                if pool._closing_sessions.get(instance_id) is entry:
                    pool._closing_sessions.pop(instance_id)


def _closing_entry_for(pool: BrowserPool, instance_id: str, session: BrowserSession | None) -> ClosingSession | None:
    """The retained ``ClosingSession`` for ``instance_id``, if any (and, when
    ``session`` is given, only if it is draining that exact object)."""
    existing = pool._closing_sessions.get(instance_id)
    if existing is not None and (session is None or existing.session is session):
        return existing
    return None


def _record_recently_evicted(pool: BrowserPool, instance_id: str, session: BrowserSession) -> None:
    pool._recently_evicted[instance_id] = bool(getattr(session, "_crashed", False))
    if len(pool._recently_evicted) > pool._RECENTLY_EVICTED_CAP:
        del pool._recently_evicted[next(iter(pool._recently_evicted))]


def _running_loop_available(instance_id: str, session: BrowserSession) -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        log.warning("octowright.pool.external_close_no_loop", instance_id=instance_id, kind=session.kind)
        return False
    return True


def accept_external_close_nowait(
    pool: BrowserPool,
    instance_id: str,
    *,
    expected_session: BrowserSession | None,
    reason: SessionCloseReason,
) -> ClosingSession | None:
    """Synchronous external-close acceptance seam for Playwright close/crash
    callbacks (``listeners._wire_close_evictor``) and a dead shared driver
    (``driver_relaunch._snapshot_and_evict``). No ``await`` between steps.

    Returns the retained ``ClosingSession`` (a caller may later
    ``await entry.reservation.wait()`` on it), or ``None`` when there is
    nothing to do (unknown id, a stale identity a keep-id rekey already moved
    past, or no running loop to schedule durable cleanup on)."""
    session = pool._sessions.get(instance_id)
    active = session is not None and (expected_session is None or session is expected_session)
    if not active:
        # Either genuinely unknown, or a late signal for an identity a
        # keep-id rekey already moved past -- unless an explicit close (or an
        # earlier external signal) already owns this id's teardown, in which
        # case that coordinator is the one to hand back, untouched.
        return _closing_entry_for(pool, instance_id, expected_session)
    assert session is not None, "narrows for type-checkers; `active` proved it above"  # nosec B101
    # Close admission FIRST, before removing visibility or scheduling
    # anything -- an operation that started admission just before this must
    # not observe an "active" session whose registry entry then vanishes
    # without the gate ever having said so.
    session._operation_gate.mark_closed_external()
    pool._sessions.pop(instance_id, None)
    _record_recently_evicted(pool, instance_id, session)
    # An explicit close already draining this exact session takes ownership
    # -- its own coordinator will take the SessionClosedError teardown-only
    # branch once it is granted; don't spin up a second owner.
    existing = _closing_entry_for(pool, instance_id, session)
    if existing is not None:
        return existing
    if not _running_loop_available(instance_id, session):
        return None
    reservation = session._operation_gate.reserve_external_teardown("external_close")
    entry = ClosingSession(session=session, reservation=reservation)
    pool._closing_sessions[instance_id] = entry
    _spawn_close_coordinator(pool, instance_id, entry, reason=reason)
    return entry


async def handoff_browser(
    pool: BrowserPool,
    old_instance_id: str,
    *,
    headed: bool | None = None,
    close_original: bool = True,
    accept_stateless: bool = False,
) -> dict[str, Any]:
    # Wrap the full handoff in a span so close + launch nest cleanly under it
    # in the trace tree. Without a parent span the only signal an operator
    # had was two unrelated `browser.close` / `browser.launch` events with
    # no semantic link back to the originating handoff request.
    source = pool.get(old_instance_id)
    # Snapshot every field we need BEFORE awaiting close. A Playwright
    # external-close eviction (context.close / browser.disconnected /
    # page.close) can fire between this point and `pool.close()`, popping
    # the session out of the pool. If we re-read ``source`` attributes
    # later they'd still be valid (SimpleNamespace-style ref), but the
    # important invariant is that we don't depend on the session staying
    # registered: the launch of the replacement must succeed whether or
    # not close raced an eviction.
    source_kind = source.kind
    source_label = source.label
    source_profile = source.profile
    source_user_data_dir = getattr(source, "user_data_dir", None)
    source_stabilize = getattr(source, "stabilize", False)
    source_trace = getattr(source, "trace", False)
    source_har_path = getattr(source, "har_path", None)
    source_protected = getattr(source, "protected", False)
    source_protected_reason = getattr(source, "protected_reason", "explicit")
    target_url = getattr(source.page, "url", None) or source.url
    with span(
        "octowright.browser.handoff",
        old_instance_id=old_instance_id,
        kind=source_kind,
        headed=headed,
        close_original=close_original,
        accept_stateless=accept_stateless,
    ):
        if source_profile is None and source_user_data_dir is None and not accept_stateless:
            raise ValueError(
                "handoff would be stateless: source has no profile/user_data_dir; pass accept_stateless=True to proceed"
            )
        if not close_original and (source_profile is not None or source_user_data_dir is not None):
            raise ValueError(
                "persistent handoff requires close_original=True so the state directory can be safely reused"
            )

        session_scoped = source_profile is None and source_user_data_dir is not None
        close_result: dict[str, Any] | None = None
        if close_original:
            try:
                # force=True: the caller explicitly opted into close_original
                # (close-then-relaunch of the same logical browser, state
                # preserved) — not a destructive agent close, so a protected
                # (e.g. headed-by-default) source must not refuse here the
                # way an explicit browser_close would.
                close_result = await pool.close(old_instance_id, force=True)
            except KeyError:
                # The session was evicted (external-close listener fired)
                # between pool.get() above and this close(). Treat as
                # "already closed" and proceed to launch the replacement
                # so the user isn't left with no browser.
                log.warning(
                    "octowright.browser.handoff.close_raced_eviction",
                    old_instance_id=old_instance_id,
                    kind=source_kind,
                )
                close_result = None

        # Don't overwrite the prior HAR — handoff gets a fresh sibling path.
        next_har = rotate_har_path(source_har_path)
        launch = await pool.launch(
            kind=source_kind,
            url=target_url,
            headed=headed,
            label=source_label,
            profile=source_profile,
            stabilize=source_stabilize,
            trace=source_trace,
            har=bool(source_har_path),
            har_path=str(next_har) if next_har else None,
            session=session_scoped,
            protected=source_protected,
        )
        # resolve_protected() always stamps reason="explicit" whenever an
        # explicit (non-None) protected value is passed in — which we just did
        # with source_protected above, to carry the boolean across the
        # handoff. That correctly preserves the protected bit but loses the
        # ORIGINAL reason (e.g. "headed_default"), which the tailored
        # close-refusal message keys off. Restore it post-hoc now that the new
        # session is registered in the pool. Use maybe_get (not get): some
        # unit tests stub out ``pool.launch`` entirely, so there may be no
        # real session behind the returned instance_id — nothing to patch in
        # that case.
        new_session = pool.maybe_get(launch["instance_id"])
        if new_session is not None:
            new_session.protected_reason = source_protected_reason

        return {
            "ok": True,
            "old_instance_id": old_instance_id,
            "new_instance_id": launch["instance_id"],
            "old_closed": bool(close_result and close_result.get("closed")),
            "profile": source_profile,
            "kind": source_kind,
            "url": target_url,
            "har_path": launch.get("har_path"),
        }


async def shutdown_pool(pool: BrowserPool) -> None:
    # Use the ``shutdown`` reason so MCP clients can distinguish daemon exit
    # from an agent explicitly calling ``browser_close_all``.
    await pool.close_all(_reason="shutdown", force=True)
    # close_all only reaches sessions that were still in `_sessions`. An
    # external-close or canceled-close coordinator that had already left
    # `_sessions` (but is still draining in `_closing_sessions`) must not be
    # abandoned mid-teardown just because it's no longer pool-visible.
    async with pool._sessions_lock:
        stragglers = list(pool._closing_sessions.values())
    for entry in stragglers:
        try:
            await entry.reservation.wait()
        except Exception as exc:
            log.warning("octowright.pool.shutdown_straggler_close_failed", error=repr(exc))
    if pool._pw is not None:
        await pool._pw.stop()
        pool._pw = None
    # Hold ``_sessions_lock`` across the snapshot-and-clear so a concurrent
    # ``_resolve_session_dir`` (which mints tmpdirs under the same lock) can't
    # slip a new entry into the dict between our iteration and ``.clear()``.
    # Without this, a launch racing shutdown would create a tmpdir that the
    # cleanup loop has already iterated past, leaking the directory.
    async with pool._sessions_lock:
        tmpdirs = list(pool._session_profile_dirs.values())
        pool._session_profile_dirs.clear()
    for tmpdir in tmpdirs:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except OSError:
            pass
