# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Static guardrail for ``side_effect_get`` route registration.

``_cross_origin_blocked`` skips cross-origin protection on GET/HEAD by default
because most GETs are read-only — drive-by browser fetches from a malicious
origin can read but not mutate. The exception is GETs that drive the live
browser (live screenshot, live markdown capture). Those *must* be registered
with ``side_effect_get=True`` so the same-origin policy applies.

This audit enumerates every registered GET handler, scans its source for known
side-effect call markers, and asserts that flagged handlers were wrapped with
``side_effect_get=True``. Adding a new side-effecting GET without flipping the
flag will fail this test at PR time instead of shipping a CSRF gap.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any

from starlette.routing import Route, WebSocketRoute

from octowright.http.routes.registry import all_routes

# Substrings whose presence in a handler's source flag it as side-effecting:
# - ``live.capture_`` — generates artifacts on the running browser.
# - ``.page.screenshot``/``.page.evaluate``/``.page.snapshot`` — direct page
#   mutations against the live session.
# - ``await live.`` followed by an action verb — anything that drives the live
#   browser past a read.
#
# The list is intentionally conservative — false positives only force a flag
# flip, which is the safe direction. False negatives are the failure mode we
# care about (missing a real side-effect would silently leave the CSRF hole
# open).
_SIDE_EFFECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\blive\.capture_\w+"),
    re.compile(r"\.aria_snapshot\b"),
    re.compile(r"\.page\.screenshot\b"),
    re.compile(r"\.page\.evaluate\b"),
    re.compile(r"\.page\.snapshot\b"),
)


def _unwrap_to_handler(endpoint: Callable[..., Any]) -> Callable[..., Any]:
    """``guard_sensitive_http`` returns a ``functools.wraps``-decorated wrapper.
    Walk ``__wrapped__`` back to the original handler so ``inspect.getsource``
    sees the real body, not the 3-line guard wrapper."""
    current = endpoint
    while hasattr(current, "__wrapped__"):
        current = current.__wrapped__  # type: ignore[attr-defined]
    return current


def _is_side_effect(handler: Callable[..., Any]) -> bool:
    try:
        source = inspect.getsource(handler)
    except (OSError, TypeError):
        return False
    return any(p.search(source) for p in _SIDE_EFFECT_PATTERNS)


def _has_side_effect_get_flag(endpoint: Callable[..., Any]) -> bool:
    """``guard_sensitive_http`` stashes the resolved bool in the wrapper's
    closure cell named ``side_effect_get``. Read it back via the closure so
    the audit doesn't depend on a separate registration metadata table."""
    closure = getattr(endpoint, "__closure__", None)
    code = getattr(endpoint, "__code__", None)
    if closure is None or code is None:
        return False
    for name, cell in zip(code.co_freevars, closure, strict=False):
        if name == "side_effect_get":
            return bool(cell.cell_contents)
    return False


def test_side_effect_get_routes_are_flagged() -> None:
    """Every GET route whose handler drives the live browser must be
    registered with ``side_effect_get=True``. New side-effecting GETs that
    forget the flag fail here."""
    offenders: list[str] = []
    audited: list[str] = []
    for route in all_routes():
        if not isinstance(route, Route):
            continue
        if "GET" not in (route.methods or set()):
            continue
        handler = _unwrap_to_handler(route.endpoint)
        if not _is_side_effect(handler):
            continue
        audited.append(route.path)
        if not _has_side_effect_get_flag(route.endpoint):
            offenders.append(f"{route.path} -> {handler.__qualname__}")
    # Sanity: the audit should always find SOME side-effecting GETs; an empty
    # list means our detection patterns no longer match anything and the test
    # is silently passing.
    assert audited, "audit found no side-effecting GETs — patterns may be stale"
    assert not offenders, (
        "GET routes drive the live browser but were not registered with side_effect_get=True; "
        "this leaves a CSRF hole — wrap with guard_sensitive_http(..., side_effect_get=True): " + ", ".join(offenders)
    )


def test_sensitive_api_http_routes_are_guarded() -> None:
    """Every sensitive /api HTTP route must be wrapped by guard_sensitive_http."""
    exemptions = {"/api/health"}
    offenders: list[str] = []
    for route in all_routes():
        if not isinstance(route, Route) or not route.path.startswith("/api/"):
            continue
        if route.path in exemptions:
            continue
        if not getattr(route.endpoint, "__octowright_sensitive_guard__", False):
            offenders.append(route.path)

    assert not offenders, "sensitive /api HTTP routes are missing guard_sensitive_http: " + ", ".join(offenders)


def test_api_websocket_routes_are_explicitly_audited() -> None:
    """WebSocket routes cannot use guard_sensitive_http, so enumerate the approved in-handler guard."""
    sockets = [route for route in all_routes() if isinstance(route, WebSocketRoute) and route.path.startswith("/api/")]
    assert [route.path for route in sockets] == ["/api/sessions/{id}/tail"]

    endpoint = sockets[0].endpoint
    assert getattr(endpoint, "__name__", "") == "TailEndpoint"
    source = inspect.getsource(endpoint)
    assert "sensitive_allowed_for_connection" in source
    assert "websocket_origin_allowed" in source
