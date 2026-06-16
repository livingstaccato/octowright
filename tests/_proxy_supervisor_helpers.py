# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse


def _request(method: str, request_id: str = "r1") -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params={"x": 1}))
    )


def _tools_call(tool: str, request_id: str = "tc1") -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=request_id,
                method="tools/call",
                params={"name": tool, "arguments": {}},
            )
        )
    )


def _tools_call_with_token(tool: str, request_id: str, token: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=request_id,
                method="tools/call",
                params={"name": tool, "arguments": {}, "_meta": {"progressToken": token}},
            )
        )
    )


def _progress(token: str, progress: float = 1.0) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/progress",
                params={"progressToken": token, "progress": progress},
            )
        )
    )


def _notification(method: str) -> SessionMessage:
    return SessionMessage(JSONRPCMessage(root=JSONRPCNotification(jsonrpc="2.0", method=method, params={"x": 1})))


def _response(request_id: str = "r1") -> SessionMessage:
    return SessionMessage(JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=request_id, result={"ok": True})))


class FailingRemoteWrite:
    async def send(self, _message: SessionMessage) -> None:
        raise anyio.ClosedResourceError
