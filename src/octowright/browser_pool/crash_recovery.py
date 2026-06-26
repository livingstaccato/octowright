# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Auto-recover renderer crashes by reloading the page; bounded and observable.

A Playwright ``page.on("crash")`` means the renderer process died ("Aw, Snap")
but the browser process is usually still alive, so ``page.reload()`` brings the
page back without losing the session (same instance_id, profile, context). This
module turns that into automatic, bounded recovery wired off the crash listener.

Bounding (so a page that crashes on every reload doesn't loop forever): a
per-session attempt counter capped at ``CRASH_RECOVERY_MAX``, with a crash-loop
reset — if it has been quiet for ``CRASH_RECOVERY_RESET_SECONDS`` the counter
resets, so an occasional crash over a long session keeps recovering while a tight
crash loop gives up and surfaces the session as ``crashed`` for a manual relaunch.

Observability: OTel counters plus process-lifetime tallies (``recovery_stats``)
that ``octowright_status`` surfaces, since OTel counters aren't readable back.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from provide.telemetry import get_logger

from octowright._tracing import counter

log = get_logger(__name__)

_RECOVERED = counter(
    "octowright_browser_crash_recovered_total",
    description="Renderer crashes auto-recovered by reloading the page",
)
_RECOVERY_FAILED = counter(
    "octowright_browser_crash_recovery_failed_total",
    description="Renderer-crash auto-recovery attempts whose page.reload() failed",
)

# Process-lifetime readable tallies for octowright_status (OTel counters can't be
# read back in-process). Bounded by construction — three integers.
_STATS = {"crashes": 0, "recoveries": 0, "recovery_failures": 0}


def note_crash() -> None:
    """Record that a renderer crash was observed (called from the crash listener)."""
    _STATS["crashes"] += 1


def recovery_stats() -> dict[str, int]:
    return dict(_STATS)


def reset_stats() -> None:
    """Test/operator hook to zero the tallies; not exposed as an MCP tool."""
    _STATS.update(crashes=0, recoveries=0, recovery_failures=0)


def _eligible(session: Any, *, max_recoveries: int, reset_seconds: float, now: float) -> bool:
    """Decide whether ``session`` may auto-recover now, applying the crash-loop
    reset. Mutates ``session._crash_recoveries`` (reset on a quiet gap) and
    ``session._last_crash_monotonic`` (stamped to ``now``)."""
    if now - session._last_crash_monotonic > reset_seconds:
        session._crash_recoveries = 0
    session._last_crash_monotonic = now
    return session._crash_recoveries < max_recoveries


def schedule_recovery(session: Any, page: Any) -> Any | None:
    """Schedule an async ``page.reload()`` to recover a crashed renderer, or
    return ``None`` when recovery is disabled, exhausted, or there is no running
    loop. The task is tracked on ``session._bg_tasks`` and self-removes on done."""
    from octowright.defaults import (
        CRASH_RECOVERY_ENABLED,
        CRASH_RECOVERY_MAX,
        CRASH_RECOVERY_RELOAD_TIMEOUT_MS,
        CRASH_RECOVERY_RESET_SECONDS,
    )

    if not CRASH_RECOVERY_ENABLED:
        return None
    if not _eligible(
        session, max_recoveries=CRASH_RECOVERY_MAX, reset_seconds=CRASH_RECOVERY_RESET_SECONDS, now=time.monotonic()
    ):
        log.warning(
            "octowright.crash.recovery_exhausted",
            instance_id=session.instance_id,
            attempts=session._crash_recoveries,
            max=CRASH_RECOVERY_MAX,
        )
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_recover(session, page, CRASH_RECOVERY_RELOAD_TIMEOUT_MS))
    session._bg_tasks.add(task)
    task.add_done_callback(session._bg_tasks.discard)
    return task


async def _recover(session: Any, page: Any, reload_timeout_ms: float) -> bool:
    """Reload the crashed page. On success clear ``_crashed`` and count it; on
    failure leave ``_crashed`` set so the session still reports as crashed."""
    session._crash_recoveries += 1
    iid = session.instance_id
    try:
        await _replace_crashed_page(session, page, reload_timeout_ms)
    except Exception as exc:
        _STATS["recovery_failures"] += 1
        _RECOVERY_FAILED.add(1, attributes={"kind": session.kind})
        log.warning(
            "octowright.crash.recovery_failed",
            instance_id=iid,
            attempt=session._crash_recoveries,
            error=repr(exc),
        )
        return False
    session._crashed = False
    _STATS["recoveries"] += 1
    _RECOVERED.add(1, attributes={"kind": session.kind})
    log.info("octowright.crash.recovered", instance_id=iid, attempt=session._crash_recoveries)
    try:
        session.recorder.record("page_recovered", attempt=session._crash_recoveries)
    except Exception as exc:
        log.debug("octowright.crash.recovery_recorder_failed", instance_id=iid, error=repr(exc))
    return True


async def _replace_crashed_page(session: Any, dead_page: Any, timeout_ms: float) -> None:
    """Recover by replacing the dead page, NOT reloading it.

    A crashed renderer cannot be reloaded — Playwright keeps raising
    ``Page.reload: Page crashed`` (verified against a real ``chrome-headless-shell``
    SIGSEGV). But the browser process and its context survive, so a fresh page in
    the same context, navigated to the dead page's URL, restores a working session
    under the same instance_id. The new page is wired with the same listeners
    (so a re-crash recovers too) and swapped in as the session's active page; the
    dead page is closed best-effort."""
    from octowright.browser_pool.listeners import _wire_listeners

    try:
        last_url = dead_page.url or session.url
    except Exception:
        last_url = session.url
    new_page = await session.context.new_page()
    _wire_listeners(session, new_page)
    await new_page.goto(last_url, timeout=timeout_ms)
    try:
        idx = session.pages.index(dead_page)
        session.pages[idx] = new_page
    except ValueError:
        session.pages.append(new_page)
    if session.page is dead_page:
        session.page = new_page
    session.page_count = len(session.pages)
    try:
        await dead_page.close()
    except Exception as exc:
        log.debug("octowright.crash.dead_page_close_failed", instance_id=session.instance_id, error=repr(exc))
