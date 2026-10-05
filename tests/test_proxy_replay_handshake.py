# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The bridge's replayed handshake finishes before anything else is sent.

On every connect -- the first one after an election, and each reconnect -- the
bridge replays the client's ``initialize`` on the fresh leader session. The
leader names that session (``Mcp-Session-Id``) only in its answer to it, and
mcp's streamable-HTTP client builds each POST's headers from the session id it
holds when the POST goes out, POSTing requests concurrently as tasks. So a
``notifications/initialized`` or a queued call sent before the answer arrived
went out with no session id, and the leader refused it with ``-32600 Bad
Request: Missing session ID`` -- intermittently on a fast client's first call,
and on every reconnect against a real leader
(``tests/test_bridge_leader_restart_live.py``).

The replay therefore waits, within the connect timeout, for the leader's answer
before completing the handshake and before any queued or resumed call is sent.
An error answer, a session that closes first, or no answer in time is a failed
connect: the session is dropped and nothing queued is forwarded on it.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCRequest, JSONRPCResponse

from octowright import proxy_runtime as runtime
from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import _request, _tools_call
from tests.test_proxy_early_handshake import _URL, _Election, _initialize, _initialized, _Leader
from tests.test_proxy_early_handshake import _wire as _wire_election

# How long a frame is given to (wrongly) appear before we decide none was sent.
_QUIET_SECONDS = 0.3


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _answer(request_id: Any) -> SessionMessage:
    return SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request_id, result={"protocolVersion": "2025-06-18"}))


def _refusal(request_id: Any) -> SessionMessage:
    return SessionMessage(
        JSONRPCError(jsonrpc="2.0", id=request_id, error=ErrorData(code=-32600, message="Fixture refusal"))
    )


async def _nothing_more(to_leader: Any) -> None:
    """Nothing reaches the leader within ``_QUIET_SECONDS``."""
    with anyio.move_on_after(_QUIET_SECONDS):
        frame = await to_leader.receive()
        raise AssertionError(f"sent before the replayed initialize was answered: {frame.message!r}")


async def _replayed_initialize(to_leader: Any) -> Any:
    replay = (await to_leader.receive()).message
    assert isinstance(replay, JSONRPCRequest) and replay.method == "initialize", replay
    assert str(replay.id).startswith("octowright-bridge-replay"), replay
    return replay.id


@pytest.mark.anyio
async def test_first_connect_sends_nothing_until_the_replay_is_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire_election(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_source=election, pre_leader_budget=30.0))
        with anyio.fail_after(5.0):
            await local_in.send(_initialize())
            await local_out.receive()  # answered locally
            await local_in.send(_initialized())
            await local_in.send(_request("tools/list", "list-1"))
            await anyio.sleep(0.1)
            election.decide(_URL)
            await leader.wait_for(1)
            from_leader, to_leader = leader.sessions[0]
            replay_id = await _replayed_initialize(to_leader)
            await _nothing_more(to_leader)
            await from_leader.send(_answer(replay_id))
            assert (await to_leader.receive()).message.method == "notifications/initialized"
            assert (await to_leader.receive()).message.id == "list-1"
        tg.cancel_scope.cancel()


def _wire_reconnect(monkeypatch: pytest.MonkeyPatch, leader: _Leader) -> tuple[Any, Any]:
    local_in, local_out = _wire_election(monkeypatch, leader)

    async def _no_flap_backoff(_connected_at: float | None, flap_attempt: int) -> tuple[int, bool]:
        return flap_attempt, True

    monkeypatch.setattr(runtime, "_flap_backoff", _no_flap_backoff)
    return local_in, local_out


async def _first_session(leader: _Leader, local_in: Any, local_out: Any) -> None:
    """Initialize on session 1 through the leader, then leave a resumable call
    in flight on it and drop the stream."""
    await leader.wait_for(1)
    from_leader, to_leader = leader.sessions[0]
    await local_in.send(_initialize())
    assert (await to_leader.receive()).message.id == "init-1"
    await from_leader.send(_answer("init-1"))
    assert (await local_out.receive()).message.id == "init-1"
    await local_in.send(_initialized())
    assert (await to_leader.receive()).message.method == "notifications/initialized"
    await local_in.send(_tools_call("persona_list", "resumed-1"))
    assert (await to_leader.receive()).message.id == "resumed-1"
    await from_leader.send(RuntimeError("leader dropped the stream"))


@pytest.mark.anyio
async def test_reconnect_sends_nothing_until_the_replay_is_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire_reconnect(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url=_URL))
        with anyio.fail_after(5.0):
            await _first_session(leader, local_in, local_out)
            await leader.wait_for(2)
            from_leader, to_leader = leader.sessions[1]
            replay_id = await _replayed_initialize(to_leader)
            await local_in.send(_tools_call("persona_list", "queued-1"))
            await _nothing_more(to_leader)
            await from_leader.send(_answer(replay_id))
            assert (await to_leader.receive()).message.method == "notifications/initialized"
            assert {(await to_leader.receive()).message.id for _ in range(2)} == {"resumed-1", "queued-1"}
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_refused_replay_is_a_failed_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire_reconnect(monkeypatch, leader)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url=_URL))
        with anyio.fail_after(5.0):
            await _first_session(leader, local_in, local_out)
            await leader.wait_for(2)
            from_leader, to_leader = leader.sessions[1]
            replay_id = await _replayed_initialize(to_leader)
            await local_in.send(_tools_call("persona_list", "queued-1"))
            await from_leader.send(_refusal(replay_id))
            # The refused session is dropped with nothing else sent on it ...
            leftover: list[Any] = []
            with pytest.raises(anyio.EndOfStream):
                while True:
                    leftover.append((await to_leader.receive()).message)
            assert leftover == []
            # ... and the bridge reconnects, replaying the handshake first again.
            await leader.wait_for(3)
            from_leader, to_leader = leader.sessions[2]
            replay_id = await _replayed_initialize(to_leader)
            await _nothing_more(to_leader)
            await from_leader.send(_answer(replay_id))
            assert (await to_leader.receive()).message.method == "notifications/initialized"
            assert {(await to_leader.receive()).message.id for _ in range(2)} == {"resumed-1", "queued-1"}
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_replay_waits_for_its_answer_when_it_reads_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=None, request_timeout_seconds=20.0)
    sup.track_local_message(_request("initialize", "init-1"))
    sup.track_local_message(_initialized())
    to_leader_send, to_leader = anyio.create_memory_object_stream[SessionMessage](10)
    from_leader, from_leader_recv = anyio.create_memory_object_stream[Any](10)
    async with anyio.create_task_group() as tg:
        tg.start_soon(sup.replay_initialize, to_leader_send, from_leader_recv)
        with anyio.fail_after(5.0):
            replay_id = await _replayed_initialize(to_leader)
            await _nothing_more(to_leader)
            await from_leader.send(_answer(replay_id))
            assert (await to_leader.receive()).message.method == "notifications/initialized"


@pytest.mark.anyio
async def test_replay_without_an_answer_in_time_raises_and_completes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor.defaults, "BRIDGE_CONNECT_TIMEOUT_SECONDS", 0.2)
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=None, request_timeout_seconds=20.0)
    sup.track_local_message(_request("initialize", "init-1"))
    sup.track_local_message(_initialized())
    to_leader_send, to_leader = anyio.create_memory_object_stream[SessionMessage](10)
    _from_leader, from_leader_recv = anyio.create_memory_object_stream[Any](10)
    with anyio.fail_after(5.0), pytest.raises(TimeoutError):
        await sup.replay_initialize(to_leader_send, from_leader_recv)
    assert supervisor.message_method(to_leader.receive_nowait()) == "initialize"
    with pytest.raises(anyio.WouldBlock):
        to_leader.receive_nowait()


@pytest.mark.anyio
async def test_replay_on_a_session_that_closes_first_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=None, request_timeout_seconds=20.0)
    sup.track_local_message(_request("initialize", "init-1"))
    to_leader_send, _to_leader = anyio.create_memory_object_stream[SessionMessage](10)
    from_leader, from_leader_recv = anyio.create_memory_object_stream[Any](10)
    await from_leader.aclose()
    with anyio.fail_after(5.0), pytest.raises(supervisor.ReplayHandshakeError):
        await sup.replay_initialize(to_leader_send, from_leader_recv)


@pytest.mark.anyio
async def test_a_refused_replay_takes_the_connect_backoff_not_the_flap_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A session whose handshake was refused never opened: it is a failed
    connect, backed off as one, not a session that flapped."""
    leader = _Leader()
    local_in, local_out = _wire_election(monkeypatch, leader)
    flap_checks: list[float | None] = []

    async def _recording_flap_backoff(connected_at: float | None, flap_attempt: int) -> tuple[int, bool]:
        flap_checks.append(connected_at)
        return flap_attempt, True

    monkeypatch.setattr(runtime, "_flap_backoff", _recording_flap_backoff)
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url=_URL))
        with anyio.fail_after(5.0):
            await _first_session(leader, local_in, local_out)
            await leader.wait_for(2)
            from_leader, to_leader = leader.sessions[1]
            await from_leader.send(_refusal(await _replayed_initialize(to_leader)))
            await leader.wait_for(3)
        # Session 1 opened and then failed; session 2's handshake was refused.
        assert flap_checks[0] is not None
        assert flap_checks[1:] == [None]
        tg.cancel_scope.cancel()
