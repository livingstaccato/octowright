# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A 2026-07-28 client is told at once that the follower speaks only the
handshake era, and falls back to ``initialize``.

Such a client opens with ``server/discover`` and stamps its protocol version on
every request (``_meta``). The bridge replays ``initialize`` into each leader
session it opens, so it cannot carry a session that never had one: through it
the leader answered every such request ``Bad Request: Missing session ID``,
after waiting out the election. The follower now refuses that era itself,
leader or not, with the error the SDK's ``mode='auto'`` probe reads as a legacy
server.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.client._probe import negotiate_auto
from mcp.client.session import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCRequest, JSONRPCResponse
from mcp_types import PROTOCOL_VERSION_META_KEY, UNSUPPORTED_PROTOCOL_VERSION, UnsupportedProtocolVersionErrorData
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, LATEST_MODERN_VERSION

from octowright import mcp_identity
from octowright.proxy_handshake import refuse_modern_request
from tests.test_proxy_early_handshake import _URL, _Election, _initialize, _initialized, _Leader, _start, _wire


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _enveloped(method: str, request_id: str, version: Any = LATEST_MODERN_VERSION) -> SessionMessage:
    params = {"_meta": {PROTOCOL_VERSION_META_KEY: version}}
    return SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=params))


def test_the_refusal_names_the_handshake_versions_and_the_one_requested() -> None:
    refusal = refuse_modern_request(_enveloped("server/discover", "d1"))
    assert refusal is not None
    error = refusal.message
    assert isinstance(error, JSONRPCError)
    assert error.id == "d1"
    assert error.error.code == UNSUPPORTED_PROTOCOL_VERSION
    data = UnsupportedProtocolVersionErrorData.model_validate(error.error.data)
    assert data.supported == list(HANDSHAKE_PROTOCOL_VERSIONS)
    assert data.requested == LATEST_MODERN_VERSION


def test_only_an_enveloped_request_other_than_initialize_is_refused() -> None:
    assert refuse_modern_request(_initialize()) is None
    enveloped_initialize = _initialize()
    enveloped_initialize.message.params["_meta"] = {PROTOCOL_VERSION_META_KEY: LATEST_MODERN_VERSION}
    assert refuse_modern_request(enveloped_initialize) is None  # initialize is the legacy handshake
    plain = SessionMessage(JSONRPCRequest(jsonrpc="2.0", id="l1", method="tools/list", params={"_meta": {}}))
    assert refuse_modern_request(plain) is None  # a bare _meta is not an envelope
    assert refuse_modern_request(_initialized()) is None


def test_an_unparseable_requested_version_still_names_what_is_served() -> None:
    refusal = refuse_modern_request(_enveloped("tools/list", "l1", version=7))
    assert refusal is not None
    assert refusal.message.error.data == {"supported": list(HANDSHAKE_PROTOCOL_VERSIONS)}


@pytest.mark.anyio
async def test_an_auto_client_falls_back_to_initialize_on_the_refusal() -> None:
    """The SDK's own connect-time negotiation, against the follower's answers."""
    to_client_send, to_client_recv = anyio.create_memory_object_stream[SessionMessage | Exception](10)
    to_server_send, to_server_recv = anyio.create_memory_object_stream[SessionMessage](10)
    seen: list[str] = []

    async def follower() -> None:
        async for message in to_server_recv:
            root = message.message
            if not isinstance(root, JSONRPCRequest):
                continue
            seen.append(root.method)
            refusal = refuse_modern_request(message)
            if refusal is not None:
                await to_client_send.send(refusal)
            elif root.method == "initialize":
                result = mcp_identity.local_initialize_result(root.params)
                await to_client_send.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=root.id, result=result)))

    async with anyio.create_task_group() as tg:
        tg.start_soon(follower)
        async with ClientSession(to_client_recv, to_server_send) as session:
            with anyio.fail_after(5.0):
                await negotiate_auto(session)
            assert session.initialize_result is not None
            assert session.initialize_result.server_info.name == mcp_identity.SERVER_NAME
        tg.cancel_scope.cancel()
    assert seen[:2] == ["server/discover", "initialize"]


@pytest.mark.anyio
async def test_discover_is_refused_at_once_while_the_election_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(1.0):
            await election.started.wait()
            await local_in.send(_enveloped("server/discover", "d1"))
            reply = (await local_out.receive()).message
        assert isinstance(reply, JSONRPCError)
        assert reply.id == "d1"
        assert reply.error.code == UNSUPPORTED_PROTOCOL_VERSION
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_an_enveloped_request_never_reaches_a_live_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(5.0):
            await local_in.send(_initialize())
            await local_out.receive()
            await local_in.send(_initialized())
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            replay = (await to_leader.receive()).message
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay.id, result={})))
            assert (await to_leader.receive()).message.method == "notifications/initialized"
            await local_in.send(_enveloped("tools/list", "l1"))
            await local_in.send(SessionMessage(JSONRPCRequest(jsonrpc="2.0", id="l2", method="tools/list")))
            reply = (await local_out.receive()).message
            assert isinstance(reply, JSONRPCError) and reply.id == "l1"
            assert (await to_leader.receive()).message.id == "l2"  # l1 never went
        tg.cancel_scope.cancel()
