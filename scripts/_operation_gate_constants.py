# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared constants for the operation-gate architecture scanner.

Split out of ``check_operation_gate_architecture.py`` (kept under the
repository's LOC-per-file convention). Pure data -- no AST logic, no
dependency on the scanner's own modules.
"""

from __future__ import annotations

PLAYWRIGHT_ROOT_ATTRS = frozenset({"page", "pages", "context", "browser", "active_frame"})
PLAYWRIGHT_CHAIN_ATTRS = frozenset(
    {
        "locator",
        "keyboard",
        "screencast",
        "frames",
        "goto",
        "click",
        "fill",
        "press",
        "evaluate",
        "aria_snapshot",
        "screenshot",
        "new_page",
        "title",
        "wait_for_selector",
        "query_selector",
        "wait_for_url",
        "route",
        "unroute",
        "set_input_files",
        "hover",
        "drag_and_drop",
        "select_option",
        "go_back",
        "set_viewport_size",
        "expect_popup",
        "wait_for_load_state",
        "inner_text",
        "count",
        "close",
        "is_closed",
        "opener",
        "on",
        "add_init_script",
        "expose_binding",
        "start",
        "stop",
        "save_as",
        "suggested_filename",
        "url",
        "main_frame",
        "video",
        "path",
        "tracing",
        # Dialog verbs (page.on("dialog", ...) handler surface) -- not in the
        # original set; production has a genuine event-critical dialog
        # handler (session/core_interaction_mixin.py:_handle_dialog._act)
        # whose access is otherwise invisible to this attribute set.
        "accept",
        "dismiss",
        # Route fulfillment verbs (page.route(pattern, handler) callback).
        "fulfill",
        "continue_",
        "abort",
        # ConsoleMessage fields (page.on("console", ...) handler surface).
        "type",
        "text",
        # WebSocketFrame fields (websocket.on("framesent"/"framereceived", ...)).
        "payload",
        "is_binary",
        # Response/Request fields (page.on("response"/"requestfailed", ...)).
        # "status"/"request" are gated on an already-tainted base, so this
        # does not collide with httpx's differently-shaped Response
        # (status_code, not status) used elsewhere in this codebase.
        "method",
        "resource_type",
        "status",
        "status_text",
        "failure",
        "request",
    }
)
APPROVED_BYPASS_CLASSES = frozenset(
    {
        "event-critical",
        "teardown-only",
        "cached-property-only",
        "launch-time-before-session-publication",
    }
)

SEED_BASE_NAMES = frozenset({"session", "self", "live", "source"})
SEED_PARAM_NAMES = frozenset(
    {
        "page",
        "frame",
        "target",
        "context",
        "locator",
        # Empirically-confirmed page aliases in production call sites (crash
        # recovery, popup handling, new-tab redirection). Not a blind suffix
        # match: "on_frame" (a screencast frame-DIRECTION callback, unrelated
        # to a Playwright Frame) is a real collision that suffix matching on
        # "frame" would wrongly seed, so each alias is listed explicitly.
        "new_page",
        "dead_page",
        "crashed_page",
        "target_page",
        # A Playwright Dialog delivered to page.on("dialog", ...); paired
        # with the "accept"/"dismiss" additions to PLAYWRIGHT_CHAIN_ATTRS.
        "dialog",
        # A Playwright Route delivered to page.route(pattern, handler); paired
        # with the "fulfill"/"continue_"/"abort" additions below. Also used
        # elsewhere (scenarios.py) as a plain config-dict parameter name, but
        # those only call dict methods absent from PLAYWRIGHT_CHAIN_ATTRS, so
        # seeding it there is inert, not a false positive.
        "route",
        # A Playwright WebSocket delivered to page.on("websocket", ...). Name
        # collides with Starlette's own ``websocket: WebSocket`` handler
        # parameter, but that explicit non-Playwright annotation overrides
        # this naming heuristic via annotation_signal, so no false positive.
        "websocket",
        # Playwright Response/Request delivered to page.on("response"/
        # "requestfailed", ...). Collides by name with httpx.Response/
        # Request and Starlette's Request, but those are always explicitly
        # annotated in this codebase, so annotation_signal suppresses the
        # name-based seed for them (verified: none of their call sites touch
        # an attribute this scanner also treats as Playwright-shaped).
        "request",
        "response",
    }
)
SESSION_TYPE_NAMES = frozenset({"BrowserSession", "SessionLike"})
GENERIC_TYPING_MODULES = frozenset({"typing", "typing_extensions"})

TARGET_METHOD = "_target"
GATE_METHOD = "operation"
CLOSE_METHOD = "close_operation"
FORWARDER_FUNCTION = "browser_operation"
DECORATOR_NAME = "gated_operation"
EXCLUDED_DIR_NAMES = frozenset({"terminal", "__pycache__"})
