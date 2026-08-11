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
