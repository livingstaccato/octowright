# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower keeps reading stdin while a forwarded call waits for a leader.

A call that has no leader to go to yet (the election is pending, or the leader
is being reconnected to) waits. Frames behind it are still read: a ``ping`` is
answered at once, and a ``notifications/cancelled`` for a call that never
reached the leader withdraws it -- the leader never sees it and the client gets
no response for it, as MCP says a cancelled request gets none. Ordinary
requests keep their order.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCNotification, JSONRPCResponse

from octowright import defaults, proxy_intake
from tests._proxy_supervisor_helpers import _request, _tools_call
from tests.test_proxy_early_handshake import _URL, _Election, _initialize, _initialized, _Leader, _start, _wire


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _cancelled(request_id: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCNotification(
            jsonrpc="2.0", method="notifications/cancelled", params={"requestId": request_id, "reason": "user"}
        )
    )


@pytest.mark.anyio
async def test_a_ping_behind_a_call_waiting_for_the_election_is_answered_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        await local_in.send(_request("tools/list", "list-1"))
        await local_in.send(_request("ping", "p1"))
        with anyio.fail_after(1.0):
            reply = await local_out.receive()
        assert isinstance(reply.message, JSONRPCResponse)
        assert reply.message.id == "p1"
        assert reply.message.result == {}
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_cancelling_calls_that_wait_for_the_election_withdraws_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """The head of the queue (waiting for a writer) and one queued behind it are
    both withdrawn; the call after them still goes, and nothing about the
    withdrawn ones reaches the leader or the client."""
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(5.0):
            await local_in.send(_request("tools/list", "a"))
            await local_in.send(_request("tools/list", "b"))
            await local_in.send(_request("tools/list", "c"))
            await anyio.sleep(0.05)
            await local_in.send(_cancelled("b"))
            await local_in.send(_cancelled("a"))
            await anyio.sleep(0.05)
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            first = (await to_leader.receive()).message
            assert first.id == "c"
            await anyio.sleep(0.1)
            with pytest.raises(anyio.WouldBlock):
                to_leader.receive_nowait()  # neither the withdrawn calls nor their cancellations
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id="c", result={})))
            reply = await local_out.receive()
        assert reply.message.id == "c"  # the first and only answer the client gets
        with pytest.raises(anyio.WouldBlock):
            local_out.receive_nowait()
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_cancelled_call_the_leader_has_is_cancelled_there_and_not_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call already delivered gets the cancellation forwarded; it is no longer
    the bridge's to resume, so a reconnect does not send it again."""
    monkeypatch.setattr(defaults, "IDEMPOTENCY_ENABLED", True)
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(10.0):
            await local_in.send(_initialize())
            await local_out.receive()
            await local_in.send(_initialized())
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            replay = (await to_leader.receive()).message
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay.id, result={})))
            assert (await to_leader.receive()).message.method == "notifications/initialized"
            await local_in.send(_tools_call("browser_list", "x"))
            assert (await to_leader.receive()).message.id == "x"
            await local_in.send(_cancelled("x"))
            forwarded: Any = (await to_leader.receive()).message
            assert forwarded.method == "notifications/cancelled"
            assert forwarded.params["requestId"] == "x"
            await from_leader.aclose()  # the session ends; the bridge reconnects
            await leader.wait_for(2)
            to_leader2, from_leader2 = leader.sessions[1][1], leader.sessions[1][0]
            replay2 = (await to_leader2.receive()).message
            await from_leader2.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay2.id, result={})))
            assert (await to_leader2.receive()).message.method == "notifications/initialized"
            await local_in.send(_request("tools/list", "y"))
            assert (await to_leader2.receive()).message.id == "y"  # not a re-sent "x"
        with pytest.raises(anyio.WouldBlock):
            local_out.receive_nowait()  # no error for the cancelled call either
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_ordinary_requests_behind_a_waiting_call_keep_their_order(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, _local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(5.0):
            for request_id in ("r1", "r2", "r3"):
                await local_in.send(_request("tools/list", request_id))
            await local_in.send(_request("ping", "p1"))
            await local_in.send(_request("resources/list", "r4"))
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader = leader.sessions[0][1]
            ids = [(await to_leader.receive()).message.id for _ in range(4)]
        assert ids == ["r1", "r2", "r3", "r4"]
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_ping_behind_a_call_waiting_for_a_reconnect_is_answered_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same rule holds while the leader is being reconnected to: the new
    session's replayed handshake is not answered yet, so no writer is
    published, and a ping behind a call waiting for one is still answered."""
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(10.0):
            await local_in.send(_initialize())
            await local_out.receive()
            await local_in.send(_initialized())
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            replay = (await to_leader.receive()).message
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay.id, result={})))
            assert (await to_leader.receive()).message.method == "notifications/initialized"
            await from_leader.aclose()  # the session ends; the bridge reconnects
            await leader.wait_for(2)
            to_leader2, from_leader2 = leader.sessions[1][1], leader.sessions[1][0]
            replay2 = (await to_leader2.receive()).message  # left unanswered for now
            await local_in.send(_request("tools/list", "y"))
            await local_in.send(_request("ping", "p2"))
        with anyio.fail_after(1.0):
            reply = (await local_out.receive()).message
        assert isinstance(reply, JSONRPCResponse)
        assert reply.id == "p2"
        with anyio.fail_after(5.0):
            await from_leader2.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay2.id, result={})))
            assert (await to_leader2.receive()).message.method == "notifications/initialized"
            assert (await to_leader2.receive()).message.id == "y"
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_request_past_the_pending_cap_is_refused_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """What waits for a leader is bounded: a request arriving with the cap
    already waiting is answered with an error instead of queued, and the
    queued ones still go, in order, once a leader is there."""
    monkeypatch.setattr(proxy_intake, "MAX_PENDING_FRAMES", 3)
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=30.0)
        with anyio.fail_after(5.0):
            for request_id in ("r1", "r2", "r3", "r4"):
                await local_in.send(_request("tools/list", request_id))
            refused = (await local_out.receive()).message
            assert isinstance(refused, JSONRPCError)
            assert refused.id == "r4"
            assert "waiting for the leader" in refused.error.message
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader = leader.sessions[0][1]
            ids = [(await to_leader.receive()).message.id for _ in range(3)]
        assert ids == ["r1", "r2", "r3"]
        tg.cancel_scope.cancel()
