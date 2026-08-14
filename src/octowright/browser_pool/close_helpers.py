# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pure/near-pure helpers for ``lifecycle._coordinate_close``.

Split out of ``lifecycle.py`` (kept as free functions, called from the
coordinator) purely to keep that module under the repository's LOC ceiling --
no behavior change from when these lived directly in ``lifecycle.py``. Every
function here is reachable ONLY from ``_coordinate_close``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright._tracing import counter
from octowright.browser_pool.events import SessionClosedEvent, SessionCloseReason
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.session_manifest import remove_session as remove_manifest_session
from octowright.session_manifest import run_manifest_transaction_async

if TYPE_CHECKING:
    from octowright.browser_pool.pool import BrowserPool
    from octowright.session import BrowserSession

log = get_logger(__name__)

# Bumped by every external-close coordinator; an explicit close doesn't (it
# isn't an eviction). Moved here from listeners.py -- the coordinator now
# decides "was this close explicit or external".
EVICTED = counter(
    "octowright_browser_evicted_total",
    description="Browsers removed from the pool by an external close signal (not pool.close)",
)


def recorder_close_reason(reason: SessionCloseReason) -> str | None:
    if reason == "crashed":
        return "crashed"
    if reason in ("user_close", "external_disconnect"):
        return "external"
    return None  # agent_close / shutdown keep the current reason-less row


async def remove_active_identity(pool: BrowserPool, instance_id: str, session: BrowserSession) -> None:
    async with pool._sessions_lock:
        if pool._sessions.get(instance_id) is session:
            pool._sessions.pop(instance_id, None)


def log_secondary_teardown_error(session: BrowserSession, exc: BaseException, *, primary: BaseException) -> None:
    log.warning(
        "octowright.browser.close_teardown_secondary_error",
        instance_id=getattr(session, "instance_id", None),
        kind=getattr(session, "kind", None),
        error=repr(exc),
        primary_error=repr(primary),
    )


async def prepare_then_teardown(
    session: BrowserSession,
    preparation: Any,
    recorder_reason_value: str | None,
) -> tuple[object | None, BaseException | None]:
    """Always attempt the teardown, even when ``preparation`` fails first.
    Returns ``(prepared, error)`` where ``error`` is the FIRST failure; a
    teardown failure following a preparation failure is logged as secondary,
    not swapped in as the shared outcome."""
    prepared: object | None = None
    error: BaseException | None = None
    if preparation is not None:
        try:
            prepared = await preparation(session)
        except BaseException as exc:
            error = exc
    try:
        await session._teardown_after_close_cutoff(reason=recorder_reason_value)
    except BaseException as exc:
        if error is None:
            error = exc
        else:
            log_secondary_teardown_error(session, exc, primary=error)
    return prepared, error


def close_response(session: BrowserSession) -> dict[str, Any]:
    return {
        "closed": True,
        "log_path": str(session.log_path),
        "video_path": str(session.video_path) if session.video_path else None,
        "trace_path": str(session.trace_path) if session.trace_path else None,
        "har_path": str(session.har_path) if session.har_path else None,
    }


async def remove_manifest_best_effort(instance_id: str) -> None:
    try:
        await run_manifest_transaction_async(remove_manifest_session, instance_id)
    except Exception as exc:
        log.warning("octowright.session_manifest.remove_failed", instance_id=instance_id, error=repr(exc))


def publish_close_once(session: BrowserSession, instance_id: str, reason: SessionCloseReason) -> None:
    """Log + publish exactly once. ``reason`` picks the log line/metric:
    explicit close keeps ``octowright.browser.closed``; an external-origin
    reason is ``octowright.browser.evicted_externally`` plus the eviction
    counter -- what the listeners used to emit directly before this task."""
    if reason in ("user_close", "external_disconnect", "crashed"):
        EVICTED.add(1, attributes={"kind": session.kind})
        log.info(
            "octowright.browser.evicted_externally",
            instance_id=instance_id,
            kind=session.kind,
            profile=session.profile,
            log_path=str(session.log_path),
        )
    else:
        log.info(
            "octowright.browser.closed",
            instance_id=instance_id,
            kind=session.kind,
            profile=session.profile,
            log_path=str(session.log_path),
        )
    session_event_bus.publish_nowait(
        SessionClosedEvent(
            instance_id=instance_id,
            kind=session.kind,
            label=session.label,
            profile=session.profile,
            reason=reason,
            log_path=str(session.log_path),
        )
    )
