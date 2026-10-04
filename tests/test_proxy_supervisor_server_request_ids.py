# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A leader-initiated REQUEST never settles a client request that shares its id.

JSON-RPC ids are allocated independently by each side, so the leader's own
``roots/list`` (or sampling/elicitation) request can carry id ``1`` while the
client's ``tools/call`` id ``1`` is in flight. The bridge used to pop the
client's in-flight entry for any frame with a matching id: the server request
was forwarded, the client's tracking was gone, its real response was then
dropped as a duplicate, and the deadline watchdog could no longer answer it --
the client waited forever on a call the leader had completed.
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCRequest, JSONRPCResponse

from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import _tools_call


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _server_request(request_id: str | int, method: str = "roots/list") -> SessionMessage:
    return SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params={}))


def _drain(recv: anyio.abc.ObjectReceiveStream[SessionMessage]) -> list[SessionMessage]:
    frames: list[SessionMessage] = []
    while True:
        try:
            frames.append(recv.receive_nowait())
        except anyio.WouldBlock:
            return frames


@pytest.mark.anyio
async def test_server_request_with_a_colliding_id_leaves_the_client_call_pending() -> None:
    _in_send, in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    bridge = supervisor.BridgeSupervisor(local_read=in_recv, local_write=out_send, request_timeout_seconds=30.0)

    bridge.track_local_message(_tools_call("browser_navigate", request_id="1"))
    await bridge.forward_remote_message(_server_request("1"))

    assert "1" in bridge._in_flight, "a server request must not settle the client's pending call"

    await bridge.forward_remote_message(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id="1", result={"ok": True})))

    frames = _drain(out_recv)
    kinds = [type(f.message).__name__ for f in frames]
    assert kinds == ["JSONRPCRequest", "JSONRPCResponse"], (
        "both the server request and the real response reach the client"
    )
    assert bridge._in_flight == {}


@pytest.mark.anyio
async def test_server_request_with_a_colliding_id_leaves_the_deadline_armed() -> None:
    _in_send, in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    bridge = supervisor.BridgeSupervisor(local_read=in_recv, local_write=out_send, request_timeout_seconds=30.0)

    bridge.track_local_message(_tools_call("browser_navigate", request_id="7"))
    await bridge.forward_remote_message(_server_request("7", method="sampling/createMessage"))
    bridge._in_flight["7"].deadline = -1.0

    await bridge._expire_overdue(now=0.0, reset_slot=None)

    errors = [f for f in _drain(out_recv) if type(f.message).__name__ == "JSONRPCError"]
    assert len(errors) == 1, "the watchdog still answers the client's call"
