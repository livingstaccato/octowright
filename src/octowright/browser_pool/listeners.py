# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright._tracing import counter
from octowright.browser_pool import crash_recovery
from octowright.browser_pool.events import SessionClosedEvent, SessionCloseReason, SessionCrashedEvent
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.session import BrowserSession

_EVICTED = counter(
    "octowright_browser_evicted_total",
    description="Browsers removed from the pool by an external close signal (not pool.close)",
)
_CRASHED = counter(
    "octowright_browser_crashed_total",
    description="Browser pages that fired a Playwright crash event (page.on('crash'))",
)

if TYPE_CHECKING:
    from octowright.browser_pool.pool import BrowserPool

log = get_logger(__name__)


def _wire_listeners(session: BrowserSession, page: Any) -> None:
    """Attach per-page listeners (dialog, download, close, framenavigated) to a page.
    Called for both the initial page at launch AND any popup page opened mid-session.

    The close + framenavigated handlers are looked up off the session object so
    that the same hook installed at launch time can later trip eviction or log
    a user-initiated navigation respectively. Both attributes are populated by
    ``_wire_close_evictor`` (which runs immediately after the very first
    ``_wire_listeners`` call inside ``BrowserPool.launch``).
    """
    page.on("dialog", session._handle_dialog)
    page.on("download", session._handle_download)
    page.on("response", session._handle_response)
    page.on("requestfailed", session._handle_request_failed)
    page.on("websocket", session._handle_websocket)
    page.on("load", lambda: session._schedule_markdown_capture(page=page, force=True))
    # If the close evictor has already attached its per-page handler, wire it
    # on this page too. For the initial launch page this is a no-op (the
    # attribute hasn't been set yet); _wire_close_evictor will install the
    # handler explicitly on the initial page right after it sets the attr.
    page_close_handler = getattr(session, "_on_page_close", None)
    if page_close_handler is not None:
        page.on("close", page_close_handler)
    page_crash_handler = getattr(session, "_on_page_crash", None)
    if page_crash_handler is not None:
        page.on("crash", page_crash_handler)
    framenav_handler = getattr(session, "_make_framenavigated_handler", None)
    if framenav_handler is not None:
        page.on("framenavigated", framenav_handler(page))


def _wire_close_evictor(pool: BrowserPool, session: BrowserSession) -> None:
    """When the underlying browser/context/all-pages is closed externally (OS
    close button, crash, persistent-context flush, etc.), drop the session from
    the pool registry so `pool.list_sessions()` and dashboard `/api/sessions`
    stop reporting it as live.

    Three independent signals are wired so that whichever Playwright fires
    first wins:

    1. ``session.context.on("close", _evict)`` — fires when the browser
       context closes cleanly. Persistent contexts always emit this.
    2. ``session.browser.on("disconnected", _evict)`` — fires when the
       underlying browser PROCESS dies (the OS close button on the last
       window kills the chromium process; the context-close event may not
       arrive before the connection drops). Persistent contexts have
       ``browser is None`` and skip this hook.
    3. ``page.on("close", _on_page_close)`` — installed on every page. When
       a page closes we check whether ANY page on the session is still open;
       if not, we treat that as the session being gone. Some browsers leave
       the context alive after the last page closes and wait for an idle
       timeout; we don't want to wait.

    The three signals coexist via the idempotent ``_evict`` callback —
    ``pool._sessions.pop(instance_id, None)`` returns ``None`` on the second
    and subsequent calls and the handler bails silently.

    Idempotent — safe if the session was already explicitly closed via
    ``pool.close(id)``. In the explicit-close path, ``pool.close`` removes
    the entry from ``_sessions`` BEFORE the underlying ``context.close()``
    fires its event, so the ``pop`` call below returns ``None`` and this
    handler bails silently (no double-log, no double-close on the recorder).
    """
    instance_id = session.instance_id

    def _evict(*_: Any) -> None:
        existing = pool._evict_session_nowait(instance_id)
        if existing is None:
            # Already removed by an explicit pool.close — that path logs
            # "octowright.browser.closed" itself. Stay silent.
            return
        try:
            from octowright.session_manifest import remove_session as _manifest_remove_session

            _manifest_remove_session(instance_id)
        except Exception as exc:
            log.warning("octowright.session_manifest.remove_failed", instance_id=instance_id, error=repr(exc))
        _EVICTED.add(1, attributes={"kind": session.kind})
        log.info(
            "octowright.browser.evicted_externally",
            instance_id=instance_id,
            kind=session.kind,
            profile=session.profile,
            log_path=str(session.log_path),
        )
        # Notify MCP clients that the session is gone. The disconnect event
        # alone can't tell "user closed the window" from "process died", so we
        # default to ``user_close`` — UNLESS a ``page.on("crash")`` fired on this
        # session first (``session._crashed``), which upgrades it to a definite
        # ``crashed``.
        reason: SessionCloseReason = "crashed" if getattr(session, "_crashed", False) else "user_close"
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
        # Best-effort: record an external-close marker in the recording so
        # post-mortem inspection shows the session ended unexpectedly. Both
        # calls may raise if the recorder was already closed by an in-flight
        # session.close() — swallow it.
        try:
            session.recorder.record("close", reason=reason if reason == "crashed" else "external")
            session.recorder.close()
        except Exception as exc:
            # Swallow per the silent-swallow policy: the recorder may already
            # be closed by an in-flight session.close(). Log so post-mortem
            # diagnosis is possible.
            log.debug(
                "octowright.evict.recorder_close_failed",
                instance_id=instance_id,
                error=repr(exc),
            )

    def _on_page_close(*_: Any) -> None:
        # Cascade to full eviction only when no page on the session is still
        # open. Single-page-of-many close (e.g. a popup being dismissed by
        # the user) is not a session death.
        try:
            still_open = [p for p in session.pages if not p.is_closed()]
        except Exception as exc:
            # On ambiguous enumeration failure, do NOT evict — a spurious
            # eviction removes a session that may still have live pages,
            # producing confusing tool failures. Log and bail out; the next
            # close signal (context close / browser disconnect) will catch
            # a truly dead session.
            log.debug(
                "octowright.evict.page_enumeration_failed",
                instance_id=instance_id,
                error=repr(exc),
            )
            return
        if not still_open:
            _evict()

    def _on_page_crash(crashed_page: Any = None, *_: Any) -> None:
        # Playwright fires Page 'crash' with the crashing Page as the argument
        # (renderer process died — "Aw, Snap" / Target.crashed). Fall back to the
        # session's primary page if the arg is ever absent. The browser process
        # itself is usually still alive, so
        # we do NOT evict here; we mark the session so that IF it is then evicted
        # (a crash that brings the process down → disconnected), ``_evict``
        # reports a definite ``crashed`` instead of an ambiguous ``user_close``.
        # We also publish a proactive crash notification so the client learns the
        # page is dead immediately, not only on its next failing tool call.
        session._crashed = True
        _CRASHED.add(1, attributes={"kind": session.kind})
        crash_recovery.note_crash()
        log.warning(
            "octowright.browser.page_crashed",
            instance_id=instance_id,
            kind=session.kind,
            profile=session.profile,
            log_path=str(session.log_path),
        )
        session_event_bus.publish_nowait(
            SessionCrashedEvent(
                instance_id=instance_id,
                kind=session.kind,
                label=session.label,
                profile=session.profile,
                scope="renderer",
                log_path=str(session.log_path),
            )
        )
        # Best-effort recorder marker for post-mortem inspection.
        try:
            session.recorder.record("page_crash")
        except Exception as exc:
            log.debug("octowright.crash.recorder_failed", instance_id=instance_id, error=repr(exc))
        # Auto-recover the dead renderer: the browser process is usually still
        # alive, so a bounded page.reload() heals the session without losing it.
        crash_recovery.schedule_recovery(session, crashed_page or session.page)

    # Expose the per-page close + crash handlers so ``_wire_listeners`` can
    # attach them to the initial page AND any popup page registered later via
    # ``context.on("page", session._register_popup)``.
    session._on_page_close = _on_page_close
    session._on_page_crash = _on_page_crash

    session.context.on("close", _evict)
    # Ephemeral browsers fire 'disconnected' on the Browser when the underlying
    # process dies. Some Playwright builds also expose a Browser handle on
    # persistent contexts; wire it when present for extra resilience.
    close_handle = getattr(session, "_browser_for_close", None) or session.browser
    if close_handle is not None:
        close_handle.on("disconnected", _evict)


def _wire_user_navigation_logger(session: BrowserSession) -> None:
    """Publish a ``framenavigated`` handler factory on the session so that
    every page (initial + popups) can install a per-page listener via
    ``_wire_listeners``. The listener emits a ``user_navigation`` action in
    the JSONL action timeline whenever the main frame navigates — catching
    address-bar input, link clicks, and other browser-side navigations that
    the recorder would otherwise miss.

    Filters applied per-event: only main-frame navigations, never
    ``about:blank``, and de-duped against the most recent MCP-initiated
    ``BrowserSession.navigate(url)`` call (tracked via
    ``session._last_mcp_navigation``) to avoid double-logging when the user
    calls our own navigate tool.
    """

    def _make(target_page: Any) -> Any:
        def _on_framenavigated(frame: Any) -> None:
            try:
                if frame != target_page.main_frame:
                    return
                url = getattr(frame, "url", None)
                if not url or url == "about:blank":
                    return
                if getattr(session, "_last_mcp_navigation", None) == url:
                    return
                page_index: int | None
                try:
                    page_index = session.pages.index(target_page) if target_page in session.pages else None
                except ValueError:
                    page_index = None
                session.recorder.record("user_navigation", url=url, page_index=page_index)
                # Capture a markdown snapshot for the new destination so cache
                # stays aligned with navigation history (for the currently
                # new destination.
                session._schedule_markdown_capture(page=target_page)
            except Exception as e:  # pragma: no cover - defensive
                log.debug("octowright.framenavigated.swallowed", error=repr(e))

        return _on_framenavigated

    # Expose the factory so ``_wire_listeners`` can install the handler on
    # the initial page AND any later popup page.
    session._make_framenavigated_handler = _make
