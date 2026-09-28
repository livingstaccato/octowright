# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Auto-recover renderer crashes by replacing the dead page; bounded + observable.

A Playwright ``page.on("crash")`` means the renderer process died ("Aw, Snap")
but the browser process and its context are usually still alive. The crashed page
object itself can NOT be reloaded — ``page.reload()`` / ``page.goto()`` keep
raising ``Page crashed`` (verified live against a ``chrome-headless-shell``
SIGSEGV) — so recovery opens a FRESH page in the surviving context, navigates it
to the dead page's URL, and swaps it in. The session keeps its instance_id,
profile, and context. This module wires that off the crash listener.

A navigation that fails -- refused by the SSRF policy (``guarded_navigation``)
or any network error -- does not fail the recovery: the fresh page is already
wired, so the session recovers onto it and says why, as the incident's
``navigation_error`` and the ``browser_recovered`` event's
``navigation_error``. Whether it is elsewhere (``recovered_elsewhere``) is
decided by where the page actually is, not by the error: a load that timed out
after its navigation committed has recovered AT its last URL. A fresh page
that itself crashes while loading does fail the recovery, and its crash is not
recovered a second time.

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
import contextlib
import time
import weakref
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from provide.telemetry import get_logger

from octowright import ssrf_guard
from octowright._tracing import counter
from octowright.browser_pool import incidents
from octowright.browser_pool.events import RecoveryOutcome
from octowright.session._protocols import SessionLike
from octowright.session.operation.gate import (
    OperationGateInvariantError,
    SessionClosedError,
    SessionClosingError,
    SessionOperationAbortedError,
)

if TYPE_CHECKING:
    from octowright.session.core import BrowserSession

log = get_logger(__name__)


def _publish_recovered(
    session: Any,
    outcome: RecoveryOutcome,
    navigation_error: str | None = None,
    *,
    recovered_elsewhere: bool = False,
) -> None:
    """Publish the accurate recovery outcome so the MCP client learns whether the
    crash self-healed (keep going), self-healed onto a page that is not at its
    last URL (navigate again), or it must relaunch. Best-effort; never raises."""
    from octowright.browser_pool.session_event_bus import SessionRecoveredEvent, session_event_bus

    with contextlib.suppress(Exception):
        session_event_bus.publish_nowait(
            SessionRecoveredEvent(
                instance_id=session.instance_id,
                kind=session.kind,
                label=session.label,
                profile=session.profile,
                outcome=outcome,
                attempts=session._crash_recoveries,
                log_path=str(session.log_path),
                navigation_error=navigation_error,
                recovered_elsewhere=recovered_elsewhere,
            )
        )


def _safe_url(page: Any, session: Any) -> str:
    """Best-effort URL of a (possibly crashed) page for incident context."""
    try:
        return page.url or session.url
    except Exception:
        return session.url


_RECOVERED = counter(
    "octowright_browser_crash_recovered_total",
    description="Renderer crashes auto-recovered by reloading the page",
)
_RECOVERY_FAILED = counter(
    "octowright_browser_crash_recovery_failed_total",
    description="Renderer-crash auto-recovery attempts whose page replacement failed",
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
    """Schedule async recovery (page replacement) for a crashed renderer, or
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
    url = _safe_url(page, session)
    if not _eligible(
        session, max_recoveries=CRASH_RECOVERY_MAX, reset_seconds=CRASH_RECOVERY_RESET_SECONDS, now=time.monotonic()
    ):
        log.warning(
            "octowright.crash.recovery_exhausted",
            instance_id=session.instance_id,
            attempts=session._crash_recoveries,
            max=CRASH_RECOVERY_MAX,
        )
        incidents.record(
            incidents.CATEGORY_RENDERER_CRASH,
            instance_id=session.instance_id,
            kind=session.kind,
            url=url,
            outcome="exhausted",
            attempts=session._crash_recoveries,
        )
        _publish_recovered(session, "exhausted")
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_recover(session, page, CRASH_RECOVERY_RELOAD_TIMEOUT_MS, url))
    session._bg_tasks.add(task)
    task.add_done_callback(session._bg_tasks.discard)
    return task


async def _recover(session: Any, page: Any, reload_timeout_ms: float, url: str) -> bool:
    """Durable system operation: no ordinary queue timeout, so recovery waits
    behind whatever operation was running when the renderer crashed rather
    than racing/timing out against it. Invalidated (not retried) if the
    session closes or the gate breaks before this ticket is admitted --
    there is nothing left to recover."""
    try:
        async with session.operation("crash_recovery", wait_timeout_seconds=None):
            return await _recover_owned(session, page, reload_timeout_ms, url)
    except (
        SessionClosingError,
        SessionClosedError,
        OperationGateInvariantError,
        SessionOperationAbortedError,
    ):
        log.info("octowright.crash.recovery_invalidated", instance_id=session.instance_id)
        return False


async def _recover_owned(session: Any, page: Any, reload_timeout_ms: float, url: str) -> bool:
    """Replace the crashed page. On success clear ``_crashed`` and count it; on
    failure leave ``_crashed`` set so the session still reports as crashed. Either
    way an incident record is appended so the outcome is visible in status.

    Runs entirely inside ``_recover``'s ``crash_recovery`` lease; only this
    function publishes the recovered/failed outcome, so a recovery invalidated
    before admission (session closing/closed) never claims to have repaired a
    browser it never touched."""
    session._crash_recoveries += 1
    iid = session.instance_id
    try:
        navigation_error, elsewhere = await _replace_crashed_page(session, page, reload_timeout_ms, url)
    except Exception as exc:
        _STATS["recovery_failures"] += 1
        _RECOVERY_FAILED.add(1, attributes={"kind": session.kind})
        log.warning(
            "octowright.crash.recovery_failed",
            instance_id=iid,
            attempt=session._crash_recoveries,
            error=repr(exc),
        )
        _record_incident(session, url, "failed")
        _publish_recovered(session, "failed")
        return False
    session._crashed = False
    _STATS["recoveries"] += 1
    _RECOVERED.add(1, attributes={"kind": session.kind})
    log.info("octowright.crash.recovered", instance_id=iid, attempt=session._crash_recoveries)
    incident = _record_incident(session, url, "recovered")
    if navigation_error is not None:
        if elsewhere:
            # Recovered onto a usable page that is NOT the last URL; say why.
            log.warning("octowright.crash.recovered_url_failed", instance_id=iid, url=url, error=navigation_error)
        else:
            # At the last URL, but its load did not finish (a timeout after commit).
            log.info("octowright.crash.recovered_load_incomplete", instance_id=iid, url=url, error=navigation_error)
        incident["navigation_error"] = navigation_error
        incident["recovered_elsewhere"] = elsewhere
    # Published only once the outcome is whole: a client told plain "recovered"
    # carries on against a page that never reached its URL.
    _publish_recovered(session, "recovered", navigation_error, recovered_elsewhere=elsewhere)
    # H5a: snapshot the recovered page so a postmortem has a frame, not just a marker.
    # Record the incident before awaiting the screenshot; slow runners must not
    # observe "recovered" stats without a visible incident.
    screenshot = await _capture_recovery_screenshot(session)
    incident["screenshot"] = screenshot
    try:
        session.recorder.record("page_recovered", attempt=session._crash_recoveries)
    except Exception as exc:
        log.debug("octowright.crash.recovery_recorder_failed", instance_id=iid, error=repr(exc))
    return True


async def _capture_recovery_screenshot(session: SessionLike) -> str | None:
    """Best-effort screenshot of the recovered page for postmortem. Writes next to
    the session recording (already under RECORDINGS_DIR, so disk-write containment
    holds). Returns the path or None; never raises.

    Enters its own ``crash_recovery`` lease around the direct ``page.screenshot``
    call: called from ``_recover_owned`` it re-enters the same task's existing
    lease for free, but it stays safe if a test or embedder calls it directly."""
    try:
        async with session.operation("crash_recovery", wait_timeout_seconds=None):
            path = session.log_path.with_suffix(f".recovery-{session._crash_recoveries}.png")
            await session.page.screenshot(path=str(path))
            return str(path)
    except Exception as exc:
        log.debug("octowright.crash.recovery_screenshot_failed", instance_id=session.instance_id, error=repr(exc))
        return None


def _record_incident(session: Any, url: str, outcome: str, *, screenshot: str | None = None) -> dict[str, Any]:
    return incidents.record(
        incidents.CATEGORY_RENDERER_CRASH,
        instance_id=session.instance_id,
        kind=session.kind,
        url=url,
        outcome=outcome,
        attempts=session._crash_recoveries,
        screenshot=screenshot,
    )


#: Fresh pages a recovery has opened, and whether each has crashed. A crash of
#: one belongs to the recovery loading it (:func:`claim_replacement_crash`),
#: not to a new recovery. Weak, so an entry lives only as long as its page; one
#: whose recovery succeeded is removed, since from then on it is the session's
#: page and its next crash is recovered like any other.
_REPLACEMENTS: weakref.WeakKeyDictionary[Any, bool] = weakref.WeakKeyDictionary()

#: How long a failed load of a replacement waits for its crash to be reported.
#: Chromium's ``goto`` raises ``net::ERR_ABORTED`` a beat BEFORE the page's
#: crash event (measured, Playwright 1.62); one round trip to the page lets the
#: event arrive, and against a crashed page it fails at once (``Target crashed``).
_CRASH_SETTLE_SECONDS = 2.0


class ReplacementCrashedError(RuntimeError):
    """The fresh page a recovery opened crashed while loading the last URL."""


def claim_replacement_crash(page: Any) -> bool:
    """Whether *page* is a recovery's replacement; if so, note that it crashed.

    Called by the page crash listener before it schedules a recovery: the
    recovery loading *page* reports the crash as its own failure, so a second
    recovery must not be scheduled for it.
    """
    if page is None:
        return False
    try:
        if page not in _REPLACEMENTS:
            return False
        _REPLACEMENTS[page] = True
    except TypeError:  # a page double that cannot be weakly referenced
        return False
    return True


def _same_url(page_url: Any, last_url: str) -> bool:
    """Whether *page_url* is *last_url*, as the browser spells it back.

    The browser normalises what it was asked for (``http://h`` comes back as
    ``http://h/``, a host lowercased, a default port dropped), and a fragment
    does not change the document.
    """
    if not isinstance(page_url, str):
        return False

    def key(url: str) -> tuple[str, str, int | None, str, str]:
        parts = urlsplit(url)
        try:
            port = parts.port
        except ValueError:
            port = None
        if (parts.scheme, port) in {("http", 80), ("https", 443)}:
            port = None
        return parts.scheme.lower(), (parts.hostname or "").lower(), port, parts.path or "/", parts.query

    return key(page_url) == key(last_url)


async def _settle_crash_signal(session: SessionLike, page: Any) -> None:
    """One bounded round trip to *page*, so a crash chromium reports late has arrived.

    Re-enters the caller's ``crash_recovery`` lease, as the other helpers here do."""
    try:
        async with (
            session.operation("crash_recovery", wait_timeout_seconds=None),
            asyncio.timeout(_CRASH_SETTLE_SECONDS),
        ):
            await page.evaluate("1")
    except Exception as exc:
        log.debug("octowright.crash.replacement_probe_failed", error=repr(exc))


async def _discard_replacement(session: SessionLike, new_page: Any) -> None:
    """Take a replacement that cannot be used out of the session and close it.

    The context ``page`` event may already have put it in ``session.pages``.
    Re-enters the caller's ``crash_recovery`` lease, as the other helpers here do.
    """
    async with session.operation("crash_recovery", wait_timeout_seconds=None):
        if new_page in session.pages:
            session.pages.remove(new_page)
            session.page_count = len(session.pages)
        if new_page.is_closed():
            return
        try:
            await new_page.close()
        except Exception as exc:
            log.debug("octowright.crash.replacement_close_failed", instance_id=session.instance_id, error=repr(exc))


async def _take_dead_page_slot(session: SessionLike, dead_page: Any, new_page: Any) -> None:
    """Swap *new_page* in for *dead_page* in ``session.pages`` and as the active page.

    Re-enters the caller's ``crash_recovery`` lease, as the other helpers here do."""
    async with session.operation("crash_recovery", wait_timeout_seconds=None):
        # Put the replacement in the DEAD page's slot rather than at the end, so
        # page indices stay stable across a recovery. Agents hold indices from
        # page_list/page_switch; appending would shift every index at or after the
        # crashed slot and silently retarget later page-indexed operations.
        if dead_page in session.pages:
            dead_index = session.pages.index(dead_page)
            if new_page in session.pages:
                # The context "page" event already appended it — move, don't dup.
                session.pages.remove(new_page)
                # Removing an earlier element shifts the dead page's slot left.
                dead_index = session.pages.index(dead_page)
            session.pages[dead_index] = new_page
        elif new_page not in session.pages:
            session.pages.append(new_page)
        if session.page is dead_page:
            session.page = new_page
        session.page_count = len(session.pages)


async def _replace_crashed_page(
    session: SessionLike, dead_page: Any, timeout_ms: float, last_url: str
) -> tuple[str | None, bool]:
    """Recover by replacing the dead page, NOT reloading it.

    A crashed renderer cannot be reloaded — Playwright keeps raising
    ``Page.reload: Page crashed`` (verified against a real ``chrome-headless-shell``
    SIGSEGV). But the browser process and its context survive, so a fresh page in
    the same context, navigated to the dead page's URL, restores a working session
    under the same instance_id. The new page is wired with the same listeners
    (so a re-crash recovers too) and swapped in as the session's active page; the
    dead page is closed best-effort.

    Enters its own ``crash_recovery`` lease around this direct Playwright/
    active-target access: called from ``_recover_owned`` it re-enters the same
    task's existing lease for free, but it stays safe if a test or embedder
    calls it directly.

    Returns ``(navigation_error, elsewhere)``: why the navigation of
    ``last_url`` failed -- the SSRF policy's refusal of it (or of a hop it
    redirects to), or any other navigation failure (DNS, reset, timeout) --
    else ``None``; and whether the page is somewhere other than ``last_url``,
    judged by where it actually is. A load that timed out after its
    navigation committed leaves the page AT ``last_url``, so that is not
    elsewhere. A failed navigation does not fail the recovery: the new page is
    already in the context and wired, so raising would orphan it and leave the
    dead page as ``session.page``. The session recovers onto the new page --
    blank, partly loaded or the browser's error page, and usable -- and the
    caller reports the failure instead of hiding it.

    Two exceptions fail the recovery, and take the replacement out of
    ``session.pages`` (the context ``page`` event may already have put it
    there) rather than orphan it there:

    * a replacement that is itself closed when its navigation fails (its
      context or browser went away): there is nothing usable to swap in.
      Measured (Playwright 1.62): firefox and webkit report the page closed by
      the time ``goto`` raises; chromium does too when the context closes, but
      when only the page is closed its ``goto`` raises ``net::ERR_ABORTED`` a
      beat before ``is_closed()`` turns true, so that page is swapped in and
      its own close event follows, as for a tab closed right after a recovery.
    * a replacement that crashed while loading (a last URL that crashes the
      renderer). It is not closed -- measured: chromium's ``goto`` raises
      ``net::ERR_ABORTED`` and firefox's ``Page crashed``, with the page open --
      so it looked usable, and its own crash listener scheduled another
      recovery behind this one. The listener leaves the crash of a
      replacement to the recovery loading it (:func:`claim_replacement_crash`),
      which closes it and fails."""
    from octowright.browser_pool.listeners import _wire_listeners

    async with session.operation("crash_recovery", wait_timeout_seconds=None):
        new_page = await session.context.new_page()
        _REPLACEMENTS[new_page] = False
        # Playwright fires the context "page" event for new_page(), so _register_popup
        # may have ALREADY appended + wired new_page. _wire_listeners is idempotent
        # per page, and the pages-list update below is written to converge whether or
        # not the event ran first: new_page ends up present exactly once, dead_page
        # removed — no duplicate entry, no double listeners.
        _wire_listeners(cast("BrowserSession", session), new_page)
        navigation_error: str | None = None
        try:
            await ssrf_guard.guarded_navigation(new_page.main_frame, new_page.goto(last_url, timeout=timeout_ms))
        except Exception as exc:
            if new_page.is_closed():
                await _discard_replacement(session, new_page)
                raise
            navigation_error = str(exc)
            await _settle_crash_signal(session, new_page)
        if _REPLACEMENTS.get(new_page):
            # Left in _REPLACEMENTS, so a crash event still in flight stays this recovery's.
            await _discard_replacement(session, new_page)
            raise ReplacementCrashedError(
                f"the replacement page crashed loading {last_url!r}"
                + (f": {navigation_error}" if navigation_error else "")
            )
        _REPLACEMENTS.pop(new_page, None)
        elsewhere = navigation_error is not None and not _same_url(new_page.url, last_url)
        await _take_dead_page_slot(session, dead_page, new_page)
        try:
            await dead_page.close()
        except Exception as exc:
            log.debug("octowright.crash.dead_page_close_failed", instance_id=session.instance_id, error=repr(exc))
        return navigation_error, elsewhere
