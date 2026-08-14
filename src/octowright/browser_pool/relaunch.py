# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Close-then-relaunch compound operations: ``handoff_browser`` and
``relaunch_fluid_browser``.

Split out of ``lifecycle.py`` (kept under the repository's LOC ceiling) --
both build on ``lifecycle.close_with_preparation``/``RelaunchSnapshot`` (Task
8) so the URL/profile/protection snapshot used to build the replacement
launch is taken INSIDE the close ticket, after it owns the gate, rather than
racing a concurrent navigation between an upfront read and the close.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from provide.telemetry import get_logger

from octowright._tracing import span
from octowright.browser_pool.launch_helpers import rotate_har_path
from octowright.browser_pool.lifecycle import RelaunchSnapshot, close_with_preparation

if TYPE_CHECKING:
    from octowright.browser_pool.pool import BrowserPool
    from octowright.session import BrowserSession

log = get_logger(__name__)


def _relaunch_snapshot_from_session(session: BrowserSession) -> RelaunchSnapshot:
    return RelaunchSnapshot(
        kind=session.kind,
        label=session.label,
        profile=session.profile,
        user_data_dir=getattr(session, "user_data_dir", None),
        stabilize=getattr(session, "stabilize", False),
        trace=getattr(session, "trace", False),
        har_path=getattr(session, "har_path", None),
        protected=getattr(session, "protected", False),
        protected_reason=getattr(session, "protected_reason", "explicit"),
        target_url=getattr(session.page, "url", None) or session.url,
    )


async def _prepare_handoff_snapshot(session: BrowserSession) -> RelaunchSnapshot:
    # Re-enters the coordinator's own task (exact-task reentrancy, Task 2) --
    # the close ticket already owns the gate under this same root operation
    # name, so this never queues; it exists to make the lease-holding intent
    # explicit and match every other compound-operation preparation.
    async with session.operation("browser_handoff"):
        return _relaunch_snapshot_from_session(session)


async def _prepare_relaunch_snapshot(session: BrowserSession) -> RelaunchSnapshot:
    async with session.operation("browser_relaunch_fluid"):
        return _relaunch_snapshot_from_session(session)


async def _launch_from_snapshot(
    pool: BrowserPool,
    snapshot: RelaunchSnapshot,
    *,
    headed: bool | None,
    badge: bool = False,
    ephemeral: bool = False,
) -> dict[str, Any]:
    # Don't overwrite the prior HAR — a handoff/relaunch gets a fresh sibling path.
    next_har = rotate_har_path(snapshot.har_path)
    return await pool.launch(
        kind=snapshot.kind,
        url=snapshot.target_url,
        headed=headed,
        label=snapshot.label,
        profile=snapshot.profile,
        stabilize=snapshot.stabilize,
        trace=snapshot.trace,
        har=bool(snapshot.har_path),
        har_path=str(next_har) if next_har else None,
        badge=badge,
        ephemeral=ephemeral,
        session=snapshot.profile is None and snapshot.user_data_dir is not None,
        protected=snapshot.protected,
    )


async def _restore_protection(pool: BrowserPool, instance_id: str, snapshot: RelaunchSnapshot) -> None:
    # resolve_protected() always stamps reason="explicit" whenever an
    # explicit (non-None) protected value is passed in -- which the launch
    # above just did, to carry the boolean across the handoff/relaunch. That
    # correctly preserves the protected bit but loses the ORIGINAL reason
    # (e.g. "headed_default"), which the tailored close-refusal message keys
    # off. Restore it post-hoc, through the gate's own control mutex rather
    # than a bare attribute write. Use maybe_get (not get): some unit tests
    # stub out ``pool.launch`` entirely, so there may be no real session
    # behind the returned instance_id -- nothing to patch in that case.
    new_session = pool.maybe_get(instance_id)
    if new_session is not None:
        await new_session.set_protected_state(snapshot.protected, reason=snapshot.protected_reason)


async def _handoff_without_close_owned(
    pool: BrowserPool,
    source: BrowserSession,
    *,
    headed: bool | None,
) -> dict[str, Any]:
    """The ``close_original=False`` handoff body: one ordinary lease on
    ``source`` (never ``closing``) covering the URL snapshot, the
    replacement launch, and the response -- no OTHER pre-existing session's
    lease is ever held at the same time."""
    snapshot = _relaunch_snapshot_from_session(source)
    launch = await _launch_from_snapshot(pool, snapshot, headed=headed)
    await _restore_protection(pool, launch["instance_id"], snapshot)
    return {
        "ok": True,
        "old_instance_id": source.instance_id,
        "new_instance_id": launch["instance_id"],
        "old_closed": False,
        "profile": snapshot.profile,
        "kind": snapshot.kind,
        "url": snapshot.target_url,
        "har_path": launch.get("har_path"),
    }


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
    source_kind = source.kind
    source_profile = source.profile
    source_user_data_dir = getattr(source, "user_data_dir", None)
    with span(
        "octowright.browser.handoff",
        old_instance_id=old_instance_id,
        kind=source_kind,
        headed=headed,
        close_original=close_original,
        accept_stateless=accept_stateless,
    ):
        # Pure validation -- no lease needed, and it must run before any
        # reservation so a refused call has zero side effects.
        if source_profile is None and source_user_data_dir is None and not accept_stateless:
            raise ValueError(
                "handoff would be stateless: source has no profile/user_data_dir; pass accept_stateless=True to proceed"
            )
        if not close_original and (source_profile is not None or source_user_data_dir is not None):
            raise ValueError(
                "persistent handoff requires close_original=True so the state directory can be safely reused"
            )

        if not close_original:
            async with source.operation("browser_handoff"):
                return await _handoff_without_close_owned(pool, source, headed=headed)

        try:
            # force=True: the caller explicitly opted into close_original
            # (close-then-relaunch of the same logical browser, state
            # preserved) — not a destructive agent close, so a protected
            # (e.g. headed-by-default) source must not refuse here the
            # way an explicit browser_close would. expected_session pins
            # this to the SAME source object, not just its instance_id.
            outcome = await close_with_preparation(
                pool,
                old_instance_id,
                force=True,
                reason="agent_close",
                operation_name="browser_handoff",
                preparation=_prepare_handoff_snapshot,
                expected_session=source,
            )
            close_result: dict[str, Any] | None = outcome.response
            snapshot = cast(RelaunchSnapshot, outcome.prepared)
        except KeyError:
            # The session was evicted (external-close listener fired)
            # between pool.get() above and this close(). Treat as
            # "already closed" and proceed to launch the replacement so the
            # user isn't left with no browser -- the preparation callback
            # never ran, so fall back to a pre-close read of ``source``.
            log.warning(
                "octowright.browser.handoff.close_raced_eviction",
                old_instance_id=old_instance_id,
                kind=source_kind,
            )
            close_result = None
            snapshot = _relaunch_snapshot_from_session(source)

        launch = await _launch_from_snapshot(pool, snapshot, headed=headed)
        await _restore_protection(pool, launch["instance_id"], snapshot)

        return {
            "ok": True,
            "old_instance_id": old_instance_id,
            "new_instance_id": launch["instance_id"],
            "old_closed": bool(close_result and close_result.get("closed")),
            "profile": snapshot.profile,
            "kind": snapshot.kind,
            "url": snapshot.target_url,
            "har_path": launch.get("har_path"),
        }


async def relaunch_fluid_browser(pool: BrowserPool, instance_id: str) -> dict[str, Any]:
    source = pool.get(instance_id)
    source_kind = source.kind
    # Wrap close+launch under a parent span so the child browser.close /
    # browser.launch spans nest underneath as one fluid-mode round-trip.
    with span("octowright.browser.relaunch_fluid", instance_id=instance_id, kind=source_kind):
        try:
            # force=True: relaunch_fluid closes the source only to reopen
            # the same logical browser immediately after (state/profile
            # preserved) — it is not a destructive agent close, so a
            # protected (e.g. headed-by-default) source must not refuse
            # here the way an explicit browser_close would.
            outcome = await close_with_preparation(
                pool,
                instance_id,
                force=True,
                reason="agent_close",
                operation_name="browser_relaunch_fluid",
                preparation=_prepare_relaunch_snapshot,
                expected_session=source,
            )
            close_result: dict[str, Any] | None = outcome.response
            snapshot = cast(RelaunchSnapshot, outcome.prepared)
        except KeyError:
            log.warning(
                "octowright.browser.relaunch_fluid.close_raced_eviction",
                instance_id=instance_id,
                kind=source_kind,
            )
            close_result = None
            snapshot = _relaunch_snapshot_from_session(source)

        stateless = snapshot.profile is None and snapshot.user_data_dir is None
        result = await _launch_from_snapshot(pool, snapshot, headed=True, badge=True, ephemeral=stateless)
        await _restore_protection(pool, result["instance_id"], snapshot)
        return {
            "ok": True,
            "old_instance_id": instance_id,
            "new_instance_id": result["instance_id"],
            "old_closed": bool(close_result and close_result.get("closed")),
            "mode": "fluid",
            "launch": result,
        }
