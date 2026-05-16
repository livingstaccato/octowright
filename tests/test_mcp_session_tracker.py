# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for ``octowright.http.mcp_session_tracker``.

Covers TTL-based pruning, explicit close, and the ASGI middleware's
request/response header observation. No real ASGI server — we drive the
middleware directly with synthetic scope/messages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from octowright.http.mcp_session_tracker import (
    McpSessionTracker,
    McpSessionTrackingMiddleware,
)


def test_tracker_marks_active_and_counts() -> None:
    t = McpSessionTracker()
    assert t.active_count() == 0
    t.mark_active("session-a")
    t.mark_active("session-b")
    assert t.active_count() == 2


def test_tracker_mark_active_is_idempotent_for_same_id() -> None:
    t = McpSessionTracker()
    t.mark_active("session-a")
    t.mark_active("session-a")
    assert t.active_count() == 1


def test_tracker_mark_closed_removes_session() -> None:
    t = McpSessionTracker()
    t.mark_active("session-a")
    t.mark_closed("session-a")
    assert t.active_count() == 0


def test_tracker_mark_closed_is_safe_for_unknown_session() -> None:
    t = McpSessionTracker()
    t.mark_closed("never-seen")  # must not raise
    assert t.active_count() == 0


def test_tracker_expires_stale_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_now = [1000.0]

    def _now() -> float:
        return fake_now[0]

    monkeypatch.setattr("octowright.http.mcp_session_tracker.time.monotonic", _now)

    t = McpSessionTracker(ttl=60.0)
    t.mark_active("session-a")
    assert t.active_count() == 1

    fake_now[0] = 1059.9
    assert t.active_count() == 1

    fake_now[0] = 1061.0
    assert t.active_count() == 0


def test_tracker_reset_clears_everything() -> None:
    t = McpSessionTracker()
    t.mark_active("a")
    t.mark_active("b")
    t.reset()
    assert t.active_count() == 0


# --- middleware ---


async def _passthrough_app(_scope: Any, _receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _run_middleware(
    middleware: McpSessionTrackingMiddleware,
    *,
    method: str,
    headers: list[tuple[bytes, bytes]],
    response_headers: list[tuple[bytes, bytes]] | None = None,
    response_status: int = 200,
    scope_type: str = "http",
) -> None:
    """Drive the middleware once with a synthetic request/response."""
    import asyncio

    sent: list[dict[str, Any]] = []

    async def _send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def _receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _inner_app(_scope: Any, _r: Any, send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        start_msg: dict[str, Any] = {
            "type": "http.response.start",
            "status": response_status,
            "headers": response_headers or [],
        }
        await send(start_msg)
        await send({"type": "http.response.body", "body": b""})

    # Swap the wrapped app for this one call so each test scenario can set
    # its own response shape.
    original_app = middleware.app
    middleware.app = _inner_app  # type: ignore[assignment]
    try:
        scope: dict[str, Any] = {"type": scope_type, "method": method, "headers": headers}
        asyncio.run(middleware(scope, _receive, _send))
    finally:
        middleware.app = original_app


def test_middleware_marks_active_on_request_with_session_header() -> None:
    tracker = McpSessionTracker()
    mw = McpSessionTrackingMiddleware(_passthrough_app, tracker)

    _run_middleware(mw, method="POST", headers=[(b"mcp-session-id", b"sess-1")])

    assert tracker.active_count() == 1


def test_middleware_marks_active_when_response_introduces_session_id() -> None:
    """``initialize`` request has no session id; server response sets it."""
    tracker = McpSessionTracker()
    mw = McpSessionTrackingMiddleware(_passthrough_app, tracker)

    _run_middleware(
        mw,
        method="POST",
        headers=[],
        response_headers=[(b"Mcp-Session-Id", b"sess-new")],
    )

    assert tracker.active_count() == 1


def test_middleware_marks_closed_on_successful_delete() -> None:
    tracker = McpSessionTracker()
    tracker.mark_active("sess-1")
    mw = McpSessionTrackingMiddleware(_passthrough_app, tracker)

    _run_middleware(
        mw,
        method="DELETE",
        headers=[(b"mcp-session-id", b"sess-1")],
        response_status=204,
    )

    assert tracker.active_count() == 0


def test_middleware_does_not_close_on_failed_delete() -> None:
    """A 4xx/5xx DELETE doesn't actually terminate the session, so the count
    must stay non-zero."""
    tracker = McpSessionTracker()
    tracker.mark_active("sess-1")
    mw = McpSessionTrackingMiddleware(_passthrough_app, tracker)

    _run_middleware(
        mw,
        method="DELETE",
        headers=[(b"mcp-session-id", b"sess-1")],
        response_status=500,
    )

    assert tracker.active_count() == 1


def test_middleware_ignores_non_http_scopes() -> None:
    tracker = McpSessionTracker()
    mw = McpSessionTrackingMiddleware(_passthrough_app, tracker)

    _run_middleware(
        mw,
        method="",
        headers=[(b"mcp-session-id", b"sess-1")],
        scope_type="lifespan",
    )

    assert tracker.active_count() == 0


def test_middleware_request_without_session_header_does_not_create_entry() -> None:
    tracker = McpSessionTracker()
    mw = McpSessionTrackingMiddleware(_passthrough_app, tracker)

    _run_middleware(mw, method="GET", headers=[])

    assert tracker.active_count() == 0


def test_middleware_ignores_non_ascii_session_id_bytes() -> None:
    """Mcp-Session-Id is spec'd as ASCII. If a client sends non-ASCII bytes
    we fail closed (skip the entry) rather than crash — the UnicodeDecodeError
    branch in _extract_session_id_from_headers must be exercised."""
    tracker = McpSessionTracker()
    mw = McpSessionTrackingMiddleware(_passthrough_app, tracker)

    _run_middleware(
        mw,
        method="POST",
        headers=[(b"mcp-session-id", b"\xff\xfe\xfdnot-ascii")],
    )

    assert tracker.active_count() == 0
