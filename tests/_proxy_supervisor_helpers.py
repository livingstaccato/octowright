# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCNotification, JSONRPCRequest, JSONRPCResponse


def _request(method: str, request_id: str = "r1") -> SessionMessage:
    return SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params={"x": 1}))


def _tools_call(tool: str, request_id: str = "tc1") -> SessionMessage:
    return SessionMessage(
        JSONRPCRequest(
            jsonrpc="2.0",
            id=request_id,
            method="tools/call",
            params={"name": tool, "arguments": {}},
        )
    )


def _tools_call_with_token(tool: str, request_id: str, token: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCRequest(
            jsonrpc="2.0",
            id=request_id,
            method="tools/call",
            params={"name": tool, "arguments": {}, "_meta": {"progressToken": token}},
        )
    )


def _progress(token: str, progress: float = 1.0) -> SessionMessage:
    return SessionMessage(
        JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/progress",
            params={"progressToken": token, "progress": progress},
        )
    )


def _notification(method: str) -> SessionMessage:
    return SessionMessage(JSONRPCNotification(jsonrpc="2.0", method=method, params={"x": 1}))


def _response(request_id: str = "r1") -> SessionMessage:
    return SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request_id, result={"ok": True}))


class FailingRemoteWrite:
    async def send(self, _message: SessionMessage) -> None:
        raise anyio.ClosedResourceError


async def replay_answered(sup: Any, remote_write: Any, wire: Any) -> SessionMessage:
    """Run ``sup.replay_initialize(remote_write)`` as a connect does, playing the
    leader: read the replayed ``initialize`` off ``wire`` (what ``remote_write``
    delivers to) and answer it, since the replay waits for that answer before it
    completes the handshake. Returns the replayed frame; anything sent after it
    (``notifications/initialized``) is left on ``wire``."""
    async with anyio.create_task_group() as tg:
        tg.start_soon(sup.replay_initialize, remote_write)
        replayed = await wire.receive()
        await sup.forward_remote_message(_response(replayed.message.id))
    return replayed
