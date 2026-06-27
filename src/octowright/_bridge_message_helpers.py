# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

BRIDGE_ERROR_CODE = -32000
BRIDGE_ERROR_PREFIX = "Octowright bridge error:"
# Appended to every bridge error so the agent that receives it on a failed in-flight
# call is steered away from the observed failure mode: silently substituting a
# shell-opened browser (`open`/`xdg-open`/`start`) and reporting it as launched. A
# fully-dead leader can't send any message, so the skill + MCP server instructions
# carry the same guidance for that case; this covers the recoverable/timeout path.
BRIDGE_ERROR_GUIDANCE = (
    "This is an Octowright transport error, not a browser result. Retry ONE call; if it "
    "still fails, Octowright is disconnected — STOP and tell the user to reconnect it. "
    "Forbidden: running 'octowright restart' or 'which octowright' via shell (binary not on "
    "agent PATH; restarting the daemon closes the MCP connection, not fixes it); probing "
    "/api/health; opening URLs with shell commands (open/xdg-open/start); writing Playwright "
    "scripts as a fallback. None of these restore the MCP connection. Only the user can "
    "reconnect the MCP client. Claude Code: /mcp -> select octowright -> Reconnect. "
    "Other clients: ask which client, have them use its MCP reconnect control or restart it."
)


def message_root(message: SessionMessage) -> Any:
    return message.message.root


def message_request_id(message: SessionMessage) -> str | int | None:
    root = message_root(message)
    if isinstance(root, (JSONRPCRequest, JSONRPCResponse, JSONRPCError)):
        return root.id
    return None


def message_method(message: SessionMessage) -> str | None:
    root = message_root(message)
    if isinstance(root, (JSONRPCRequest, JSONRPCNotification)):
        return root.method
    return None


def message_tool_name(message: SessionMessage) -> str | None:
    """Return the tool name of a ``tools/call`` request (its ``params.name``), else None.

    Lets the bridge apply a per-tool in-flight deadline: the JSON-RPC ``method`` is
    always ``tools/call``, so the discriminating identity is the tool name in params.
    """
    root = message_root(message)
    if isinstance(root, JSONRPCRequest) and root.method == "tools/call":
        params = root.params
        if isinstance(params, dict):
            name = params.get("name")
            if isinstance(name, str):
                return name
    return None


def is_request(message: SessionMessage) -> bool:
    return isinstance(message_root(message), JSONRPCRequest)


def is_response(message: SessionMessage) -> bool:
    return isinstance(message_root(message), (JSONRPCResponse, JSONRPCError))


def bridge_error(request_id: str | int, reason: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCError(
                jsonrpc="2.0",
                id=request_id,
                error=ErrorData(
                    code=BRIDGE_ERROR_CODE,
                    message=f"{BRIDGE_ERROR_PREFIX} {reason} {BRIDGE_ERROR_GUIDANCE}",
                ),
            )
        )
    )
