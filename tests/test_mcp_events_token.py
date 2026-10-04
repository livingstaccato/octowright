# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Capability-token gate on the follower-only /api/mcp-events SSE channel.

The leader streams crash/close/driver notifications over /api/mcp-events; only
the follower bridge consumes it (the browser dashboard never does). It carries
the same follower->leader trust as /mcp, so it is gated by the same capability
token — a different-user/sandboxed process that can't read the 0600 lockfile
can't subscribe. Safe on by default because no browser calls it.

The token DECISION is unit-tested via ``_require_token`` (the accept path would
otherwise open an infinite SSE stream that hangs a TestClient); the immediate
403 rejection is also proven end-to-end through the real app.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.testclient import TestClient

from octowright.http.app import build_app
from octowright.http.routes.mcp_events import _require_token

_TOKEN = "test-cap-token"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _request(headers: dict[str, str] | None = None) -> Request:
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {"type": "http", "method": "GET", "headers": hdrs, "query_string": b"", "path": "/x"}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


async def _ok_handler(_request: Request) -> Response:
    return JSONResponse({"ok": True})


@pytest.mark.anyio
async def test_require_token_rejects_missing_token() -> None:
    guarded = _require_token(_ok_handler, _TOKEN)
    resp = await guarded(_request())
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_require_token_rejects_wrong_token() -> None:
    guarded = _require_token(_ok_handler, _TOKEN)
    resp = await guarded(_request({"x-octowright-token": "nope"}))
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_require_token_accepts_correct_token() -> None:
    guarded = _require_token(_ok_handler, _TOKEN)
    resp = await guarded(_request({"x-octowright-token": _TOKEN}))
    assert resp.status_code == 200


def test_require_token_is_noop_without_a_configured_token() -> None:
    # Inline / --no-singleton leaders use an empty token → no gate (back-compat).
    assert _require_token(_ok_handler, "") is _ok_handler


@pytest.mark.anyio
async def test_require_token_disabled_by_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN", "off")
    guarded = _require_token(_ok_handler, _TOKEN)
    resp = await guarded(_request())  # no token, but the knob disables the gate
    assert resp.status_code == 200


def test_mcp_events_route_rejects_missing_and_wrong_token() -> None:
    # End-to-end through the real app: the 403 is immediate (before any stream).
    app = build_app(mcp_leader=False, host="127.0.0.1", mcp_token=_TOKEN)
    with TestClient(app) as client:
        assert client.get("/api/mcp-events").status_code == 403
        assert client.get("/api/mcp-events", headers={"X-Octowright-Token": "nope"}).status_code == 403


def test_inline_leader_refuses_an_unauthenticated_subscriber(monkeypatch: pytest.MonkeyPatch) -> None:
    """An inline ``--no-singleton`` leader has no capability token, so the token
    gate is a no-op there. The route was also pairing-exempt, which left the
    crash/close/driver stream -- instance ids, labels, persona and profile
    names, log paths -- open to any local user. With no token to demand it
    must fall back to the dashboard pairing gate every other inline route has.
    """
    monkeypatch.delenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", raising=False)
    client = TestClient(build_app(mcp_token=""))
    response = client.get("/api/mcp-events")
    assert response.status_code == 401
    assert client.get("/api/mcp-events", headers={"x-octowright-token": ""}).status_code == 401


@pytest.mark.anyio
async def test_inline_stream_stops_when_the_admitting_bearer_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """On an inline leader the stream is admitted by a dashboard bearer, and
    must end when that bearer expires -- as the dashboard SSE does -- rather
    than keep delivering notifications indefinitely."""
    import asyncio
    from collections.abc import AsyncGenerator
    from typing import cast

    from starlette.applications import Starlette

    from octowright.browser_pool.events import SessionClosedEvent
    from octowright.browser_pool.session_event_bus import SessionEventBus
    from octowright.http.pairing import DASHBOARD_STATE_ATTR, DashboardPairingState, dashboard_access_ok
    from octowright.http.routes import mcp_events as mcp_event_routes

    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "1")
    bus = SessionEventBus()
    monkeypatch.setattr(mcp_event_routes, "session_event_bus", bus)
    now = [1000.0]
    state = DashboardPairingState(expected_token="token", monotonic_clock=lambda: now[0], session_ttl=5.0)
    grant = state.redeem_code(state.mint_code())
    assert grant is not None
    app = Starlette()
    setattr(app.state, DASHBOARD_STATE_ATTR, state)
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "headers": [(b"authorization", f"Bearer {grant.bearer}".encode())],
        "query_string": b"",
        "path": "/api/mcp-events",
        "app": app,
    }

    async def receive() -> dict[str, Any]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    request = Request(scope, receive)
    assert dashboard_access_ok(request)
    response = await mcp_event_routes.mcp_events_endpoint(request)
    body = cast(AsyncGenerator[bytes, None], response.body_iterator)
    assert (await anext(body)).startswith(b": ready")

    now[0] += 6.0
    bus.publish_nowait(
        SessionClosedEvent(
            instance_id="x1", kind="chromium", label=None, profile=None, reason="agent_close", log_path="/tmp/x.jsonl"
        )
    )
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(body), timeout=0.5)
