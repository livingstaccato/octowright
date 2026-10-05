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
decided by where the page actually is, whether or not the navigation raised: a
load that timed out after its navigation committed has recovered AT its last
URL, and one that loaded through a redirect to ``/login`` has not. The
exceptions are the guard's own client-redirect document, which sits AT the last
URL: a chain the guard refused or failed, or a page still showing that
document, is elsewhere -- as is a navigation that failed with anything but a
timeout, since Firefox's error page also carries the URL it could not load.
A fresh page that itself crashes -- while loading, or before the recovery has
finished swapping it in -- is not recovered by its own crash listener; the
recovery loading it opens another, within the same crash-loop bound, and ends
``exhausted`` once that is spent.

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

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from provide.telemetry import get_logger

from octowright import ssrf_guard
from octowright._tracing import counter
from octowright.browser_pool import incidents
from octowright.browser_pool.events import RecoveryOutcome
from octowright.credential_sinks import url_origin
from octowright.session._protocols import SessionLike
from octowright.session.operation.gate import (
    OperationGateInvariantError,
    SessionClosedError,
    SessionClosingError,
    SessionOperationAbortedError,
)
from octowright.session.route_carry import PageRoutes, install_page_routes, page_routes_of, rebind_page_routes
from octowright.session.timeouts import bounded

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

    A replacement that itself crashes is replaced again within the crash-loop
    bound (:func:`_replace_until_it_holds`), and ``exhausted`` once it is spent.

    Runs entirely inside ``_recover``'s ``crash_recovery`` lease; only this
    function publishes the recovered/failed/exhausted outcome, so a recovery invalidated
    before admission (session closing/closed) never claims to have repaired a
    browser it never touched."""
    session._crash_recoveries += 1
    iid = session.instance_id
    route_warnings: list[str] = []
    try:
        navigation_error, elsewhere = await _replace_until_it_holds(
            session, page, reload_timeout_ms, url, route_warnings=route_warnings
        )
    except _RecoveryExhaustedError as exc:
        _count_failure(session, exc.__cause__ or exc)
        _record_incident(session, url, "exhausted")
        _publish_recovered(session, "exhausted")
        return False
    except Exception as exc:
        _count_failure(session, exc)
        _record_incident(session, url, "failed")
        _publish_recovered(session, "failed")
        return False
    session._crashed = False
    _STATS["recoveries"] += 1
    _RECOVERED.add(1, attributes={"kind": session.kind})
    log.info("octowright.crash.recovered", instance_id=iid, attempt=session._crash_recoveries)
    incident = _record_incident(session, url, "recovered")
    if navigation_error is not None or elsewhere:
        if elsewhere:
            # Recovered onto a usable page that is NOT the last URL; say why
            # (no navigation_error: it loaded, and a redirect took it elsewhere).
            log.warning("octowright.crash.recovered_url_failed", instance_id=iid, url=url, error=navigation_error)
        else:
            # At the last URL, but its load did not finish (a timeout after commit).
            log.info("octowright.crash.recovered_load_incomplete", instance_id=iid, url=url, error=navigation_error)
        incident["navigation_error"] = navigation_error
        incident["recovered_elsewhere"] = elsewhere
    if route_warnings:
        # The dead page's mocks and page headers are re-registered on its
        # replacement (route_carry); one that could not be is said here.
        incident["route_warnings"] = route_warnings
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


def _count_failure(session: Any, exc: BaseException) -> None:
    _STATS["recovery_failures"] += 1
    _RECOVERY_FAILED.add(1, attributes={"kind": session.kind})
    log.warning(
        "octowright.crash.recovery_failed",
        instance_id=session.instance_id,
        attempt=session._crash_recoveries,
        error=repr(exc),
    )


class _RecoveryExhaustedError(RuntimeError):
    """Every replacement the crash-loop bound allows crashed; ``__cause__`` is the last one's."""


async def _replace_until_it_holds(
    session: Any, dead_page: Any, reload_timeout_ms: float, url: str, *, route_warnings: list[str] | None = None
) -> tuple[str | None, bool]:
    """:func:`_replace_crashed_page`, again for a replacement that crashed, within the crash-loop bound.

    A replacement's crash is not recovered by its own listener
    (:func:`claim_replacement_crash`), so the bound a crash loop spends --
    ``CRASH_RECOVERY_MAX`` attempts, reset after ``CRASH_RECOVERY_RESET_SECONDS``
    quiet (:func:`_eligible`) -- is spent here, one attempt per replacement,
    under the same lease: a transient crash while loading recovers on the next
    page, a URL that crashes every renderer ends ``exhausted``. Nothing is
    scheduled twice.

    The page routes to restore are read ONCE, off the page that crashed: a
    replacement that crashes while loading is discarded before it held the
    slot, so the next attempt restores the same routes, and the routes move
    to whichever replacement finally holds (``route_carry.rebind_page_routes``).
    """
    from octowright.defaults import CRASH_RECOVERY_MAX, CRASH_RECOVERY_RESET_SECONDS

    routes = page_routes_of(session, dead_page)
    while True:
        try:
            return await _replace_crashed_page(
                session, dead_page, reload_timeout_ms, url, routes=routes, route_warnings=route_warnings
            )
        except ReplacementCrashedError as exc:
            if not _eligible(
                session,
                max_recoveries=CRASH_RECOVERY_MAX,
                reset_seconds=CRASH_RECOVERY_RESET_SECONDS,
                now=time.monotonic(),
            ):
                log.warning(
                    "octowright.crash.recovery_exhausted",
                    instance_id=session.instance_id,
                    attempts=session._crash_recoveries,
                    max=CRASH_RECOVERY_MAX,
                )
                raise _RecoveryExhaustedError(str(exc)) from exc
            session._crash_recoveries += 1
            log.info(
                "octowright.crash.replacement_retry",
                instance_id=session.instance_id,
                attempt=session._crash_recoveries,
                error=str(exc),
            )
            dead_page = exc.dead_page


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
    """The fresh page a recovery opened crashed while loading the last URL.

    ``dead_page`` is the page the next attempt replaces: still the original
    one when the replacement crashed before it was swapped in, the replacement
    itself when it crashed after (it holds the slot by then).
    """

    def __init__(self, message: str, *, dead_page: Any) -> None:
        super().__init__(message)
        self.dead_page = dead_page


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
    does not change the document. The origin is compared as the credential
    origin check compares it (``credential_sinks.url_origin``), then the path
    and query.
    """
    if not isinstance(page_url, str):
        return False
    origin = url_origin(page_url)
    if origin is None:  # not http(s) with a host (about:blank, data:): as spelled
        return page_url.partition("#")[0] == last_url.partition("#")[0]
    if origin != url_origin(last_url):
        return False
    current, last = urlsplit(page_url), urlsplit(last_url.strip())
    return (current.path or "/", current.query) == (last.path or "/", last.query)


async def _settle_crash_signal(session: SessionLike, page: Any) -> None:
    """One bounded round trip to *page*, so a crash chromium reports late has arrived.

    Re-enters the caller's ``crash_recovery`` lease, as the other helpers here do.
    Its failure is caught INSIDE that lease: a ``SessionCallTimeoutError``
    escaping a gated operation fires the gate's ``on_call_timeout`` hook,
    which reports the session unresponsive (a ``browser_crashed`` with
    ``scope=unresponsive`` and an incident) for a probe that is expected to
    fail against a crashed page."""
    async with session.operation("crash_recovery", wait_timeout_seconds=None):
        try:
            await bounded(page.evaluate("1"), operation="crash_recovery_probe", timeout=_CRASH_SETTLE_SECONDS)
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
    session: SessionLike,
    dead_page: Any,
    timeout_ms: float,
    last_url: str,
    *,
    routes: PageRoutes | None = None,
    route_warnings: list[str] | None = None,
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
    judged by where it actually is whether or not the navigation raised
    or because the guard ended its chain. A load that timed out after its
    navigation committed leaves the page AT ``last_url``, so that is not
    elsewhere; a redirect to ``/login`` is. A failed navigation does not fail the recovery: the new page is
    already in the context and wired, so raising would orphan it and leave the
    dead page as ``session.page``. The session recovers onto the new page --
    blank, partly loaded or the browser's error page, and usable -- and the
    caller reports the failure instead of hiding it.

    Two exceptions end this attempt, and take the replacement out of
    ``session.pages`` (the context ``page`` event may already have put it
    there) rather than orphan it there:

    * a replacement that is itself closed when its navigation fails (its
      context or browser went away): there is nothing usable to swap in.
      Measured (Playwright 1.62): firefox and webkit report the page closed by
      the time ``goto`` raises; chromium does too when the context closes, but
      when only the page is closed its ``goto`` raises ``net::ERR_ABORTED`` a
      beat before ``is_closed()`` turns true, so that page is swapped in and
      its own close event follows, as for a tab closed right after a recovery.
      A closed replacement whose crash was reported first (firefox, measured)
      is a replacement crash instead, below.
    * a replacement that crashed while loading (a last URL that crashes the
      renderer). It is not closed -- measured: chromium's ``goto`` raises
      ``net::ERR_ABORTED`` and firefox's ``Page crashed``, with the page open --
      so it looked usable, and its own crash listener scheduled another
      recovery behind this one. The listener leaves the crash of a
      replacement to the recovery loading it (:func:`claim_replacement_crash`),
      which closes it and raises :class:`ReplacementCrashedError`, so the
      caller (:func:`_replace_until_it_holds`) opens another within the
      crash-loop bound.

    A replacement that crashes after it loaded but before the swap and the
    dead page's close have finished is still this recovery's too: it stays
    in ``_REPLACEMENTS`` until then, and raises the same error with itself as
    the page the next attempt replaces.

    The context's routes survive (``inject_headers`` is a context route), but
    a page route and a page's own headers die with the page, so the dead
    page's ``mock_route`` mocks and ``set_extra_http_headers`` headers
    (*routes*, read off *dead_page* when not given) are re-registered on the
    new page BEFORE it navigates; one that cannot be is appended to
    *route_warnings*."""
    from octowright.browser_pool.listeners import _wire_listeners

    if routes is None:
        routes = page_routes_of(session, dead_page)

    async with session.operation("crash_recovery", wait_timeout_seconds=None):
        new_page = await session.context.new_page()
        _REPLACEMENTS[new_page] = False
        # Playwright fires the context "page" event for new_page(), so _register_popup
        # may have ALREADY appended + wired new_page. _wire_listeners is idempotent
        # per page, and the pages-list update below is written to converge whether or
        # not the event ran first: new_page ends up present exactly once, dead_page
        # removed — no duplicate entry, no double listeners.
        _wire_listeners(cast("BrowserSession", session), new_page)
        warnings = await install_page_routes(session, routes, new_page)
        navigation_error, unreached = await _load_replacement(session, dead_page, new_page, timeout_ms, last_url)
        # Decided by where the page is, whether or not the navigation raised: a
        # load that timed out after commit is AT its last URL, a goto that
        # succeeded through a redirect to /login is not -- nor is a page still on
        # the guard's client-redirect document, nor a browser error page that
        # merely carries the last URL (``unreached``).
        elsewhere = (
            unreached
            or ssrf_guard.served_client_redirect_last(new_page.main_frame)
            or not _same_url(new_page.url, last_url)
        )
        await _swap_in(session, dead_page, new_page, last_url)
        rebind_page_routes(session, routes, new_page)
        if route_warnings is not None:
            route_warnings[:] = warnings
        return navigation_error, elsewhere


async def _load_replacement(
    session: SessionLike, dead_page: Any, new_page: Any, timeout_ms: float, last_url: str
) -> tuple[str | None, bool]:
    """Navigate *new_page* to *last_url*: ``(navigation_error, unreached)``, or raise if it cannot be used.

    ``unreached`` is whether the navigation is known not to have loaded
    *last_url*'s document even though the page's URL may say it did: either
    the SSRF guard refused or failed a hop of it (its own record, begun by
    ``guarded_navigation``) -- a refused LATER hop leaves the page on the
    guard's client-redirect document, which sits AT the last URL -- or it
    failed with anything but a timeout. Firefox commits its ``about:neterror``
    document with ``location.href`` set to the URL it could not load (measured,
    Playwright 1.62: ``page.url`` is still ``about:blank`` when ``goto`` raises
    ``NS_ERROR_CONNECTION_REFUSED`` and the target a beat later), so judging
    by the URL alone made the report a race against that commit. Only a
    timeout can leave the page genuinely at its last URL, committed and still
    loading.
    Re-enters the caller's ``crash_recovery`` lease, as the other helpers here do.
    """
    navigation_error: str | None = None
    unreached = False
    async with session.operation("crash_recovery", wait_timeout_seconds=None):
        try:
            await ssrf_guard.guarded_navigation(new_page.main_frame, new_page.goto(last_url, timeout=timeout_ms))
        except Exception as exc:
            if new_page.is_closed():
                await _discard_replacement(session, new_page)
                if _REPLACEMENTS.get(new_page):
                    # Crashed, then closed (measured: firefox reports the page
                    # closed by the time goto raises "Page crashed"): a
                    # replacement crash, which the next attempt replaces.
                    raise ReplacementCrashedError(
                        f"the replacement page crashed loading {last_url!r}: {exc}", dead_page=dead_page
                    ) from exc
                raise
            navigation_error = str(exc)
            unreached = not _is_timeout(exc) or (
                ssrf_guard.guards_frame(new_page.main_frame)
                and ssrf_guard.frame_chain(new_page.main_frame).refused.is_set()
            )
            await _settle_crash_signal(session, new_page)
        if _REPLACEMENTS.get(new_page):
            # Left in _REPLACEMENTS, so a crash event still in flight stays this recovery's.
            await _discard_replacement(session, new_page)
            raise ReplacementCrashedError(
                f"the replacement page crashed loading {last_url!r}"
                + (f": {navigation_error}" if navigation_error else ""),
                dead_page=dead_page,
            )
    return navigation_error, unreached


def _is_timeout(exc: BaseException) -> bool:
    """Whether *exc* is a navigation timeout: Playwright's own ``TimeoutError`` is not the builtin one."""
    return isinstance(exc, (TimeoutError, PlaywrightTimeoutError))


async def _swap_in(session: SessionLike, dead_page: Any, new_page: Any, last_url: str) -> None:
    """Put *new_page* in *dead_page*'s slot and close the dead page; raise if the replacement crashed meanwhile.

    Only once this returns is it the session's page: until the swap and the
    dead page's close have finished, a crash of it is still this recovery's
    (:func:`claim_replacement_crash`), not a fresh one that a second recovery
    would then race this one to report. Re-enters the caller's
    ``crash_recovery`` lease, as the other helpers here do.
    """
    async with session.operation("crash_recovery", wait_timeout_seconds=None):
        await _take_dead_page_slot(session, dead_page, new_page)
        try:
            await dead_page.close()
        except Exception as exc:
            log.debug("octowright.crash.dead_page_close_failed", instance_id=session.instance_id, error=repr(exc))
        if _REPLACEMENTS.get(new_page):
            # It holds the slot, so the next attempt replaces it. Left in
            # _REPLACEMENTS, so a crash event still in flight stays this recovery's.
            raise ReplacementCrashedError(
                f"the replacement page crashed after loading {last_url!r}", dead_page=new_page
            )
        _REPLACEMENTS.pop(new_page, None)
