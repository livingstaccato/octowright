# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower's side of the MCP handshake.

Before a leader exists the follower answers the handshake itself
(``answer_before_leader``), from its own ``mcp_identity``. That answer is what
the client holds for the rest of the session, and a follower runs whatever
code it started with -- a leader restart does not update it (AGENTS.md,
"Follower version skew"). So on each connect the leader's answer to the
replayed ``initialize`` is compared with what the client was told
(``compare_handshake``); the bridge records the differing fields in its
snapshot, and ``octowright_status()["bridge"]["summary"]`` counts them.

A client of the 2026-07-28 era opens with no handshake at all, and the bridge
cannot carry it, so the follower refuses that era up front
(``refuse_modern_request``) and the client falls back to ``initialize``.
"""

from __future__ import annotations

from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse
from mcp_types import PROTOCOL_VERSION_META_KEY, UNSUPPORTED_PROTOCOL_VERSION
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from provide.telemetry import get_logger

from octowright._bridge_message_helpers import message_method, message_root

log = get_logger(__name__)


async def answer_before_leader(supervisor: Any, message: SessionMessage) -> bool:
    """Answer what needs no leader; True when ``message`` was handled.

    ``initialize`` is answered from ``mcp_identity`` (the leader's own values,
    parity-tested) and kept, so the first connect replays it and swallows the
    leader's answer; the answer is kept too, as what the client was told.
    ``notifications/initialized`` is kept for that replay. ``ping`` is
    answered. Params that do not validate are left for the leader to answer.
    """
    root = message_root(message)
    method = message_method(message)
    if isinstance(root, JSONRPCNotification) and method == "notifications/initialized":
        supervisor._initialized_message = message
        return True
    if not isinstance(root, JSONRPCRequest):
        return False
    if method == "ping":
        result: dict[str, Any] | None = {}
    elif method == "initialize":
        from octowright.mcp_identity import local_initialize_result

        result = local_initialize_result(root.params)
        if result is not None:
            supervisor._initialize_message = message
            supervisor._client_handshake = result
    else:
        return False
    if result is None:
        return False
    await supervisor.local_write.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=root.id, result=result)))
    return True


def refuse_modern_request(message: SessionMessage) -> SessionMessage | None:
    """The answer to a 2026-07-28 request, or None for any other frame.

    A client of that era sends no ``initialize``: every request carries its
    protocol version in ``_meta``, starting with ``server/discover``. The bridge
    cannot carry such a session -- each connect opens an HTTP-MCP session by
    replaying the client's ``initialize``, and the leader refuses a request on
    a session that never had one (``Bad Request: Missing session ID``). So the
    follower answers at once, leader or not, with the error that names the
    versions it serves; the SDK's ``mode='auto'`` client takes that as a legacy
    server and sends ``initialize``, instead of waiting out an election for a
    refusal. ``initialize`` itself is legacy even when it carries the envelope.
    """
    root = message_root(message)
    if not isinstance(root, JSONRPCRequest) or root.method == "initialize":
        return None
    meta = root.params.get("_meta") if isinstance(root.params, dict) else None
    if not isinstance(meta, dict) or PROTOCOL_VERSION_META_KEY not in meta:
        return None
    requested = meta[PROTOCOL_VERSION_META_KEY]
    data: dict[str, Any] = {"supported": list(HANDSHAKE_PROTOCOL_VERSIONS)}
    if isinstance(requested, str):
        data["requested"] = requested
    error = ErrorData(
        code=UNSUPPORTED_PROTOCOL_VERSION,
        message="this server speaks only the initialize handshake; connect with initialize",
        data=data,
    )
    return SessionMessage(JSONRPCError(jsonrpc="2.0", id=root.id, error=error))


def compare_handshake(told: Any, answer: Any) -> list[str] | None:
    """The fields where the leader's ``answer`` (a JSON-RPC response) differs
    from the result the client was ``told``; None when either is unknown.

    Logged when they differ, since a client acting on another server's
    instructions or capabilities has no way to notice it itself.
    """
    result = getattr(answer, "result", None)
    if not isinstance(told, dict) or not isinstance(result, dict):
        return None
    from octowright.mcp_identity import handshake_differences

    mismatch = handshake_differences(told, result)
    if mismatch:
        log.warning("octowright.bridge.handshake_mismatch", fields=mismatch)
    return mismatch
