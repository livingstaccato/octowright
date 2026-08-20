# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Reasoned bypass and operation-name-forwarder inventories for the
operation-gate architecture scanner. Split out of
``check_operation_gate_architecture.py`` (kept under the repository's
LOC-per-file convention). Pure data -- ``scan_paths`` validates every entry
here against a real detected hit each run (see that module's docstring).
"""

from __future__ import annotations

OPERATION_NAME_FORWARDERS: dict[str, str] = {
    "session/operation_gate.py:gated_operation._decorate._wrapped": (
        "forwards the fixed name validated once when the decorator is constructed"
    ),
    "server/browser/_operation.py:browser_operation": (
        "forwards the literal name required and checked at every complete server workflow call site"
    ),
}

BYPASSES: dict[str, tuple[str, str]] = {
    # Resources do not belong to a published BrowserSession yet.
    "browser_pool/cleanup.py:safe_close": (
        "launch-time-before-session-publication",
        "best-effort cleanup of a context/browser whose launch never published a session",
    ),
    "ssrf_guard.py:install_navigation_guard": (
        "launch-time-before-session-publication",
        "registers the context route guard before BrowserSession construction and registry publication",
    ),
    "ssrf_guard.py:_handle_route": (
        "event-critical",
        "Playwright route callback: must answer the intercepted request promptly or the navigation "
        "hangs, and it holds no BrowserSession to take a lease from -- the navigation it is deciding "
        "on is itself running under that session's gate",
    ),
    "ssrf_guard.py:_validate_chain": (
        "event-critical",
        "runs inside the same route callback to resolve redirect hops before the request is released",
    ),
    "browser_pool/launch_helpers.py:_open_browser_context": (
        "launch-time-before-session-publication",
        "creates context/page before BrowserSession construction and registry publication",
    ),
    "browser_pool/launch_helpers.py:install_scoped_header_routes": (
        "launch-time-before-session-publication",
        "registers the scoped header context routes alongside the SSRF guard, before BrowserSession "
        "construction and registry publication",
    ),
    "browser_pool/launch_helpers.py:install_scoped_header_routes._make._handler": (
        "event-critical",
        "Playwright route callback: must release the intercepted request promptly or the navigation "
        "hangs, and it holds no BrowserSession to take a lease from -- same shape as ssrf_guard's",
    ),
    "browser_pool/visuals.py:wire_init_scripts": (
        "launch-time-before-session-publication",
        "injects context init scripts before BrowserSession registry publication",
    ),
    "browser_pool/launch_publish.py:_build_session_object": (
        "launch-time-before-session-publication",
        "captures the launch-created page video handle before registry publication",
    ),
    "browser_pool/launch_publish.py:_prepare_session_before_publication": (
        "launch-time-before-session-publication",
        "listener, binding, and trace setup completes before registry insertion",
    ),
    "browser_pool/pool.py:BrowserPool._expose_viewport_binding": (
        "launch-time-before-session-publication",
        "registers the viewport callback on a context before its session is published",
    ),
    "browser_pool/listeners.py:_wire_close_evictor": (
        "launch-time-before-session-publication",
        "installs context/browser close signals before the session is published",
    ),
    # These return or compare only Octowright-owned cached references.
    "session/core.py:BrowserSession.__post_init__": (
        "cached-property-only",
        "initializes the cached page list before the session is published without browser I/O",
    ),
    "session/core.py:BrowserSession._target": (
        "cached-property-only",
        "returns the cached active-frame/page reference without dereferencing Playwright",
    ),
    "http/routes/sessions.py:_build_live_session_detail": (
        "cached-property-only",
        "page_count falls back to len(live.pages), a length read of Octowright's own cached "
        "list with no Playwright I/O; the real aria read is separately gated below it",
    ),
    "http/discovery.py:_live_summary": (
        "cached-property-only",
        "page_count falls back to len(session.pages), a length read of Octowright's own cached "
        "list with no Playwright I/O -- the closed-session dashboard-list sibling of "
        "_build_live_session_detail's identical fallback",
    ),
    "browser_pool/relaunch.py:_relaunch_snapshot_from_session": (
        "cached-property-only",
        "every field is a synchronous Python-side attribute read -- page.url is a Playwright "
        "sync/cached property, never an IPC round-trip, same as _target's dereference-free read "
        "-- with no browser I/O; used both inside an active lease and as the documented "
        "close-race fallback snapshot in _close_with_fallback_snapshot where the gate is by "
        "definition no longer available",
    ),
    "session/screencast.py:ScreencastManager._stop_bound_owned_locked": (
        "cached-property-only",
        "reads and clears the cached _bound_page reference (no I/O) purely to decide whether "
        "there is a producer to stop at all; the real Playwright call (page.screencast.stop()) "
        "is separately gated a few lines below, and moving this read inside that gate would "
        "change its documented behavior -- a session with no bound page must return cleanly "
        "even while the gate is closing/closed, not be refused for having nothing to stop",
    ),
    # A close cutoff or external close has already made ordinary admission impossible.
    "session/core_ops_mixin.py:SessionOpsMixin._teardown_after_close_cutoff": (
        "teardown-only",
        "runs only after a reserved close owns the cutoff or for broken/external cleanup",
    ),
    "session/screencast.py:ScreencastManager._best_effort_stop_bound_locked": (
        "teardown-only",
        "shared post-close stop helper for _terminate_producer_after_close and "
        "_remove_viewer_after_close_locked; both callers already run only after close made "
        "ordinary admission impossible",
    ),
    "session/core_teardown_helpers.py:stop_trace_if_enabled": (
        "teardown-only",
        "extracted verbatim from _teardown_after_close_cutoff's own body (LOC-ceiling split, "
        "no behavior change); runs only after a reserved close owns the cutoff",
    ),
    "session/core_teardown_helpers.py:close_browser_handle_after_context_close": (
        "teardown-only",
        "extracted verbatim from _teardown_after_close_cutoff's own body (LOC-ceiling split, "
        "no behavior change); runs only after a reserved close owns the cutoff",
    ),
    "session/core_teardown_helpers.py:resolve_video_path_after_close": (
        "teardown-only",
        "extracted verbatim from _teardown_after_close_cutoff's own body (LOC-ceiling split, "
        "no behavior change); runs only after a reserved close owns the cutoff -- the third of "
        "the file's three teardown-body functions, alongside stop_trace_if_enabled and "
        "close_browser_handle_after_context_close",
    ),
    # Browser callbacks must respond synchronously or unblock the admitted call.
    "session/core_interaction_mixin.py:SessionInteractionMixin._handle_dialog._act": (
        "event-critical",
        "dialog accept/dismiss must unblock the Playwright action that already owns the gate",
    ),
    "session/core_interaction_mixin.py:SessionInteractionMixin.mock_route._handler": (
        "event-critical",
        "route fulfill must unblock the network request awaited by the active operation",
    ),
    "session/core_interaction_mixin.py:SessionInteractionMixin.inject_headers._handler": (
        "event-critical",
        "route fallback must unblock the network request awaited by the active operation",
    ),
    "browser_pool/listeners.py:_wire_listeners": (
        "event-critical",
        "attaches passive listeners to a page created by a popup event or admitted recovery",
    ),
    "browser_pool/listeners.py:_wire_close_evictor._on_page_close": (
        "event-critical",
        "last-page detection must invalidate admission synchronously with Playwright's close event",
    ),
    "browser_pool/listeners.py:_wire_close_evictor._on_page_crash": (
        "event-critical",
        "crash bookkeeping and recovery scheduling must run from the Playwright crash event",
    ),
    "session/core_io_mixin.py:SessionIOMixin._register_popup": (
        "event-critical",
        "context page event updates cached page bookkeeping synchronously; user work remains gated",
    ),
    "session/core_io_mixin.py:SessionIOMixin.attach_console": (
        "launch-time-before-session-publication",
        "registers the initial page console callback before registry publication",
    ),
    "session/core_io_mixin.py:SessionIOMixin.attach_console._on_console": (
        "event-critical",
        "copies one browser console event into Octowright's bounded cache and recorder",
    ),
    "session/core_io_mixin.py:SessionIOMixin._register_popup._on_console": (
        "event-critical",
        "copies one popup console event into Octowright's bounded cache and recorder",
    ),
    "session/core_io_mixin.py:SessionIOMixin._handle_websocket": (
        "event-critical",
        "registers passive frame/close callbacks and records browser-emitted socket metadata",
    ),
    "session/core_io_mixin.py:SessionIOMixin._handle_websocket._on_frame._handler": (
        "event-critical",
        "copies one browser-emitted websocket frame into bounded recording/cache sinks",
    ),
    "session/core_network_mixin.py:SessionNetworkMixin._handle_response": (
        "event-critical",
        "copies browser response metadata into the bounded network cache",
    ),
    "session/core_network_mixin.py:SessionNetworkMixin._handle_request_failed": (
        "event-critical",
        "copies browser failure metadata into the bounded network cache",
    ),
    "browser_pool/crash_recovery.py:_safe_url": (
        "event-critical",
        "captures the crashed page URL synchronously before scheduling durable recovery",
    ),
    "browser_pool/listeners.py:_wire_user_navigation_logger._make._on_framenavigated": (
        "event-critical",
        "passive browser-originated navigation recording cannot wait behind the operation that triggered it",
    ),
    "session/screencast.py:ScreencastManager._watch_recovery": (
        "event-critical",
        "reacts to a SessionRecoveredEvent by forwarding the session's current (synchronous, "
        "cached) page reference into rebind(), which separately re-enters the operation gate "
        "for its own Playwright calls; the watcher must not queue behind unrelated session work "
        "to notice recovery promptly",
    ),
}
