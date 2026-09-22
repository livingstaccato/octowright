# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Registered-MCP validation for the browser launch automation flag."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult

from octowright.server._state import mcp
from octowright.server.browser import lifecycle as _lifecycle


async def _call_browser_launch(
    monkeypatch: pytest.MonkeyPatch,
    value: object,
) -> tuple[CallToolResult, AsyncMock]:
    launch = AsyncMock(return_value={"instance_id": "inst-1"})
    monkeypatch.setattr(_lifecycle, "_pool_launch_with_deadline", launch)

    client_to_server_send, client_to_server_recv = anyio.create_memory_object_stream[SessionMessage](64)
    server_to_client_send, server_to_client_recv = anyio.create_memory_object_stream[SessionMessage](64)
    low = mcp._lowlevel_server
    server_task = asyncio.create_task(
        low.run(
            client_to_server_recv,
            server_to_client_send,
            low.create_initialization_options(),
            raise_exceptions=False,
        )
    )
    try:
        async with ClientSession(server_to_client_recv, client_to_server_send) as client:
            await client.initialize()
            result = await client.call_tool(
                "browser_launch",
                {
                    "url": "https://x.com",
                    "ephemeral": True,
                    "disable_automation_controlled": value,
                },
            )
            return result, launch
    finally:
        server_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await server_task


@pytest.mark.anyio
@pytest.mark.parametrize("value", ["true", "false", 0, 1])
async def test_browser_launch_rejects_non_boolean_automation_flag_at_mcp_boundary(
    monkeypatch: pytest.MonkeyPatch,
    value: object,
) -> None:
    result, launch = await _call_browser_launch(monkeypatch, value)

    assert result.is_error is True
    launch.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("value", [True, False])
async def test_browser_launch_accepts_boolean_automation_flag_at_mcp_boundary(
    monkeypatch: pytest.MonkeyPatch,
    value: bool,
) -> None:
    result, launch = await _call_browser_launch(monkeypatch, value)

    assert result.is_error is False
    launch.assert_awaited_once()
    assert launch.call_args.kwargs["disable_automation_controlled"] is value
