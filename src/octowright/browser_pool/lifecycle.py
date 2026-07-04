# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright._tracing import span
from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.events import SessionClosedEvent, SessionCloseReason
from octowright.browser_pool.launch_helpers import rotate_har_path
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.session_manifest import remove_session as remove_manifest_session

if TYPE_CHECKING:
    from octowright.browser_pool.pool import BrowserPool

log = get_logger(__name__)


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


async def close_browser(
    pool: BrowserPool,
    instance_id: str,
    *,
    force: bool = False,
    _reason: SessionCloseReason = "agent_close",
) -> dict[str, Any]:
    session = pool.get(instance_id)
    if getattr(session, "protected", False) and not force:
        raise ProtectedBrowserCloseError(
            _protected_close_message(instance_id, getattr(session, "protected_reason", "explicit"))
        )
    # Remove from the registry before awaiting session.close(); that call fires
    # close events wired by listeners, which should then no-op.
    async with pool._sessions_lock:
        if instance_id not in pool._sessions:
            raise KeyError(pool._missing_session_message(instance_id))
        session = pool._sessions.pop(instance_id)
    # Always run manifest cleanup even if session.close() raises (e.g. a
    # hung browser process) — the session is already evicted from the pool,
    # so the manifest entry would otherwise be orphaned. The session's own
    # finally block ensures the recorder closes regardless.
    try:
        await session.close()
    finally:
        try:
            remove_manifest_session(instance_id)
        except Exception as exc:
            log.warning("octowright.session_manifest.remove_failed", instance_id=instance_id, error=repr(exc))
    log.info(
        "octowright.browser.closed",
        instance_id=instance_id,
        kind=session.kind,
        profile=session.profile,
        log_path=str(session.log_path),
    )
    # Notify MCP clients that the session has closed. ``_reason`` is
    # ``agent_close`` for explicit tool calls and ``shutdown`` when the pool
    # tears down on daemon exit.
    session_event_bus.publish_nowait(
        SessionClosedEvent(
            instance_id=instance_id,
            kind=session.kind,
            label=session.label,
            profile=session.profile,
            reason=_reason,
            log_path=str(session.log_path),
        )
    )
    return {
        "closed": True,
        "log_path": str(session.log_path),
        "video_path": str(session.video_path) if session.video_path else None,
        "trace_path": str(session.trace_path) if session.trace_path else None,
        "har_path": str(session.har_path) if session.har_path else None,
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
