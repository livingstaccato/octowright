# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower's local ``initialize`` answer is the leader's, exactly.

A follower answers the handshake itself while it is still electing a leader
(``octowright.mcp_identity``), so the client's view of the server -- name,
capabilities, instructions, the negotiated protocol version -- is decided
before the leader exists. If the two drift, a client sees one server at
connect and another after it, so the comparison is against what the real
leader's low-level server answers for the same params, not against constants.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCRequest, JSONRPCResponse
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, LATEST_HANDSHAKE_VERSION

from octowright import mcp_identity

_CLIENT_INFO = {"name": "parity-test-client", "version": "0.0.0"}


def _params(version: str) -> dict[str, Any]:
    return {"protocolVersion": version, "capabilities": {}, "clientInfo": _CLIENT_INFO}


async def _leader_initialize(params: dict[str, Any]) -> dict[str, Any]:
    """What the real leader's low-level server answers ``initialize`` with."""
    from octowright.server import mcp

    low = mcp._lowlevel_server
    c2s_send, c2s_recv = anyio.create_memory_object_stream[SessionMessage | Exception](1)
    s2c_send, s2c_recv = anyio.create_memory_object_stream[SessionMessage](1)
    async with anyio.create_task_group() as tg:
        tg.start_soon(low.run, c2s_recv, s2c_send, low.create_initialization_options())
        await c2s_send.send(SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=1, method="initialize", params=params)))
        with anyio.fail_after(10):
            reply = await s2c_recv.receive()
        tg.cancel_scope.cancel()
    root = reply.message
    assert isinstance(root, JSONRPCResponse), root
    return dict(root.result)


@pytest.mark.anyio
@pytest.mark.parametrize("version", [*HANDSHAKE_PROTOCOL_VERSIONS, "1999-01-01", "not-a-version"])
async def test_local_initialize_matches_the_leader(version: str) -> None:
    params = _params(version)
    assert mcp_identity.local_initialize_result(params) == await _leader_initialize(params)


def test_unknown_version_negotiates_the_latest_handshake_version() -> None:
    result = mcp_identity.local_initialize_result(_params("1999-01-01"))
    assert result is not None
    assert result["protocolVersion"] == LATEST_HANDSHAKE_VERSION


def test_invalid_params_are_not_answered_locally() -> None:
    # The leader answers malformed params with an error; the follower does not
    # guess at one, it lets the call through to the leader instead.
    assert mcp_identity.local_initialize_result({"protocolVersion": 7}) is None
    assert mcp_identity.local_initialize_result(None) is None


def test_the_server_uses_the_one_instructions_string() -> None:
    from octowright.server import mcp

    assert mcp._lowlevel_server.create_initialization_options().instructions == mcp_identity.INSTRUCTIONS
    assert mcp._lowlevel_server.create_initialization_options().server_name == mcp_identity.SERVER_NAME
