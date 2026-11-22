# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""JSON-string mock bodies must reach Playwright as TEXT, not as a parsed object.

MCP's argument pre-parser eagerly turns ``body`` that looks like JSON into a
dict/list before its schema runs, which used to be rejected as
``Input should be a valid string`` -- exactly the failure reported when
stubbing a JSON response. The tool takes the untyped boundary value back to its
JSON text before session.mock_route sees it, so valid stubs stay strings and
JSON-shaped stubs work again; a JSON string that was already an object is
normalized without changing its content-shaped meaning.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from octowright.server._state import mcp
from octowright.server.browser import network

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _validated_args(**overrides: Any) -> dict[str, Any]:
    """Run the real MCP tool's own metadata validation on the raw kwargs, the
    point at which the JSON-string body previously became an invalid dict."""
    tool = mcp._tool_manager.get_tool("browser_mock_route")
    assert tool is not None
    raw = {"instance_id": "demo", "url_pattern": "**/api/users", "status": 200}
    raw.update(overrides)
    return tool.fn_metadata.validate_arguments(raw)


class _CapturingSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def mock_route(self, url_pattern: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"url_pattern": url_pattern, **kwargs})
        return {"ok": True, "pattern": url_pattern, "status": 200}


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> _CapturingSession:
    session = _CapturingSession()

    @asynccontextmanager
    async def _fake_operation(_pool: Any, _instance_id: str, _name: str):
        yield session

    monkeypatch.setattr(network, "browser_operation", _fake_operation)
    return session


@pytest.mark.parametrize(
    "raw_body, expected",
    [
        ('{"detail":"ok"}', '{"detail":"ok"}'),
        ('{"items":[1,2,3],"nested":{"ok":true}}', '{"items":[1,2,3],"nested":{"ok":true}}'),
        ("plain text body", "plain text body"),
        (None, None),
    ],
)
async def test_json_and_plain_bodies_stay_string(
    capture: _CapturingSession, raw_body: str | None, expected: str | None
) -> None:
    args = _validated_args(body=raw_body)
    await network.browser_mock_route(**args)
    (call,) = capture.calls
    assert call["body"] == expected
    assert call["body"] is None or isinstance(call["body"], str)


async def test_a_body_pre_parsed_by_mcp_becomes_the_same_json_object(capture: _CapturingSession) -> None:
    """Explicitly cover the MCP pre-parse output being normalized back to JSON,
    proving the stub's response body content did not silently change meaning."""
    parsed = _validated_args(body={"time": [2026, 1, 1], "ok": True})["body"]
    assert not isinstance(parsed, str), "fixture must exercise MCP's dict pre-parse"
    await network.browser_mock_route(
        instance_id="demo",
        url_pattern="**/api/users",
        status=200,
        body=parsed,
        content_type="application/json",
        headers=None,
    )
    (call,) = capture.calls
    assert isinstance(call["body"], str)
    assert json.loads(call["body"]) == {"time": [2026, 1, 1], "ok": True}
