# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The end-to-end claim behind F1: a ceiling breach must not kill the MCP connection.

``tests/session/test_operation_gate_active_timeout.py`` pins the gate-level
half of the fix (the owning task ends with ``SessionOperationAbortedError``
rather than ``cancelled()``). That is a proxy for the thing that actually
matters, and the proxy is only as good as the assumption behind it: that a
bare ``asyncio.CancelledError`` escaping a tool handler would be treated as a
transport failure rather than a tool error. This module verifies that
assumption against the real ``mcp`` library instead of asserting it in prose
-- a real ``MCPServer`` and ``ClientSession`` joined by memory object
streams, with the ceiling fired at a genuinely wedged tool call.

Why it is worth a separate module: the failure it guards against is not
"the wedged call reports the wrong error". It is that the JSON-RPC
dispatcher answers ``CONNECTION_CLOSED`` for the wedged call, for every
concurrent healthy call on the same connection, and for everything after --
and ``"Connection closed"`` is exactly the string AGENTS.md's *Transport
recovery* section trains an agent to read as a dead daemon. So the knob an
operator enables to recover from ONE wedged session would take out their
whole client session and tell them to restart the daemon.

Mutation-checked: reverting ``operation()``'s cancellation absorption
(``session/operation/gate/core.py``) turns both assertions below into
``MCPError(-32000, 'Connection closed')``.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer
from mcp.shared.message import SessionMessage

from octowright.session.operation.gate import SessionOperationGate


async def test_ceiling_breach_surfaces_as_a_tool_error_not_a_dead_connection() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    server = MCPServer(name="octowright-ceiling-probe")
    entered = asyncio.Event()
    never = asyncio.Event()

    @server.tool()
    async def wedge() -> str:
        # Stands in for any gated Playwright call against a target that has
        # stopped answering: the handler holds the gate's root lease and
        # never returns on its own.
        async with gate.operation("wedged"):
            entered.set()
            await never.wait()
        return "unreachable"

    @server.tool()
    async def healthy() -> str:
        return "ok"

    client_to_server_send, client_to_server_recv = anyio.create_memory_object_stream[SessionMessage](64)
    server_to_client_send, server_to_client_recv = anyio.create_memory_object_stream[SessionMessage](64)
    low = server._lowlevel_server
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
            wedged = asyncio.create_task(client.call_tool("wedge", {}))
            async with asyncio.timeout(5):
                await entered.wait()

            # A ceiling of 0.0 breaches unconditionally against a monotonic
            # clock, so no fake clock is needed -- what is under test is the
            # transport consequence, not the arithmetic.
            async with asyncio.timeout(5):
                assert await gate.enforce_active_timeout(0.0) is True

            # The wedged call itself: an ordinary tool ERROR RESULT, which
            # only exists because the handler raised something the mcp
            # library converts. A BaseException (CancelledError) is not
            # converted; it reaches the dispatcher's shutdown path, which
            # answers CONNECTION_CLOSED and raises MCPError here instead.
            async with asyncio.timeout(10):
                wedged_result = await wedged
            assert wedged_result.is_error is True
            assert "active-duration ceiling" in str(wedged_result.content)

            # The blast radius, which is the whole point: a call issued on
            # the SAME connection after the breach must still work.
            async with asyncio.timeout(10):
                healthy_result = await client.call_tool("healthy", {})
            assert healthy_result.is_error is False

            # And the connection itself is still live, not merely able to
            # answer one queued call.
            async with asyncio.timeout(10):
                await client.send_ping()
    finally:
        never.set()
        server_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await server_task
