# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A follower opens stdio and answers the handshake before it has a leader.

``run_supervised_proxy(leader_source=...)`` opens stdio first and runs the
election as its leader source. Until the election returns, ``initialize`` and
``ping`` are answered locally (``octowright.mcp_identity``) and the client's
``notifications/initialized`` is kept for the replay; any other request waits
for the leader -- for the pre-leader budget on top of the usual connect wait,
so a slow election delays the first call instead of failing it -- and is then
delivered in order after the replayed handshake, whose answer is swallowed.
When the election falls back to an inline leader, the bridge serves it over an
in-memory connection instead of HTTP.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from octowright import defaults, mcp_identity
from octowright import proxy_runtime as runtime
from tests._proxy_supervisor_helpers import _request

_URL = "http://127.0.0.1:6299/mcp/"
_INIT_PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _initialize(request_id: str = "init-1", version: str = "2025-06-18") -> SessionMessage:
    params = {**_INIT_PARAMS, "protocolVersion": version}
    return SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method="initialize", params=params))


def _initialized() -> SessionMessage:
    return SessionMessage(JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized"))


class _Leader:
    """One in-memory session per connect, recording what each session received."""

    def __init__(self) -> None:
        self.sessions: list[tuple[Any, Any]] = []

    @asynccontextmanager
    async def client(self, _url: str, **_k: Any):  # type: ignore[no-untyped-def]
        read_send, read_recv = anyio.create_memory_object_stream[Any](20)
        write_send, write_recv = anyio.create_memory_object_stream[SessionMessage](20)
        self.sessions.append((read_send, write_recv))
        try:
            yield (read_recv, write_send)
        finally:
            await read_send.aclose()
            await write_send.aclose()

    async def wait_for(self, count: int) -> None:
        while len(self.sessions) < count:
            await anyio.sleep(0.01)


class _Election:
    """A leader source that blocks until the test decides the outcome."""

    def __init__(self) -> None:
        self.started = anyio.Event()
        self._decided = anyio.Event()
        self._outcome: Any = None

    def decide(self, outcome: Any) -> None:
        self._outcome = outcome
        self._decided.set()

    async def __call__(self) -> Any:
        self.started.set()
        await self._decided.wait()
        return self._outcome


def _wire(monkeypatch: pytest.MonkeyPatch, leader: _Leader) -> tuple[Any, Any]:
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(runtime, "resolve_leader_token", lambda: "")
    monkeypatch.setattr(runtime, "resolve_leader_generation", lambda: "leader-A")
    monkeypatch.setattr(runtime, "streamable_http_client", leader.client)

    async def _healthy(_url: str) -> bool:
        return True

    async def _sleep_forever(*_a: Any, **_k: Any) -> None:
        await anyio.sleep_forever()

    async def _snapshot(**_k: Any) -> bool:
        return True

    monkeypatch.setattr(runtime, "leader_health_alive", _healthy)
    monkeypatch.setattr(runtime, "monitor_leader_health", _sleep_forever)
    monkeypatch.setattr(runtime, "consume_leader_notifications", _sleep_forever)
    monkeypatch.setattr(runtime.bridge_state, "record_snapshot_async", _snapshot)
    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](20)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](20)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)
    return local_in_send, local_out_recv


def _start(tg: Any, election: _Election, budget: float = 30.0) -> None:
    tg.start_soon(lambda: runtime.run_supervised_proxy(leader_source=election, pre_leader_budget=budget))


@pytest.mark.anyio
async def test_initialize_is_answered_while_the_election_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election)
        with anyio.fail_after(1.0):
            await election.started.wait()
            await local_in.send(_initialize())
            reply = await local_out.receive()
        assert isinstance(reply.message, JSONRPCResponse)
        assert reply.message.id == "init-1"
        assert reply.message.result == mcp_identity.local_initialize_result(_INIT_PARAMS)
        assert leader.sessions == []  # nobody was connected to
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_ping_is_answered_before_a_leader_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election)
        with anyio.fail_after(1.0):
            await local_in.send(_request("ping", "p1"))
            reply = await local_out.receive()
        assert isinstance(reply.message, JSONRPCResponse)
        assert reply.message.id == "p1"
        assert reply.message.result == {}
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_call_made_before_the_leader_is_delivered_after_the_handshake_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election)
        with anyio.fail_after(5.0):
            await local_in.send(_initialize())
            await local_out.receive()  # answered locally
            await local_in.send(_initialized())
            await local_in.send(_request("tools/list", "list-1"))
            await local_in.send(_request("resources/list", "res-1"))
            await anyio.sleep(0.1)
            election.decide(_URL)
            await leader.wait_for(1)
            to_leader, from_leader = leader.sessions[0][1], leader.sessions[0][0]
            frames = [await to_leader.receive() for _ in range(4)]
            replay = frames[0].message
            assert isinstance(replay, JSONRPCRequest) and replay.method == "initialize"
            assert str(replay.id).startswith("octowright-bridge-replay")
            assert frames[1].message.method == "notifications/initialized"
            assert [frames[2].message.id, frames[3].message.id] == ["list-1", "res-1"]
            # The leader's answer to the replayed initialize is swallowed: the
            # client already has one.
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=replay.id, result={})))
            await from_leader.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id="list-1", result={"tools": []})))
            reply = await local_out.receive()
        assert reply.message.id == "list-1"
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_second_initialize_is_answered_and_the_latest_is_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election)
        with anyio.fail_after(5.0):
            await local_in.send(_initialize("init-1", "2025-03-26"))
            first = await local_out.receive()
            await local_in.send(_initialize("init-2", "2025-06-18"))
            second = await local_out.receive()
            election.decide(_URL)
            await leader.wait_for(1)
            replay = (await leader.sessions[0][1].receive()).message
        assert [first.message.id, second.message.id] == ["init-1", "init-2"]
        assert first.message.result["protocolVersion"] == "2025-03-26"
        assert second.message.result["protocolVersion"] == "2025-06-18"
        assert replay.params["protocolVersion"] == "2025-06-18"
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_slow_election_delays_a_call_instead_of_failing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usual connect wait is far shorter than an election can take; the
    pre-leader budget is what keeps the call alive meanwhile."""
    monkeypatch.setattr(defaults, "BRIDGE_CONNECT_TIMEOUT_SECONDS", 0.2)
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=10.0)
        with anyio.fail_after(5.0):
            await local_in.send(_request("tools/list", "list-1"))
            await anyio.sleep(0.6)  # three connect timeouts
            election.decide(_URL)
            await leader.wait_for(1)
            delivered = (await leader.sessions[0][1].receive()).message
        assert delivered.id == "list-1"
        with pytest.raises(anyio.WouldBlock):
            local_out.receive_nowait()  # no early error went to the client
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_call_still_fails_once_the_pre_leader_budget_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "BRIDGE_CONNECT_TIMEOUT_SECONDS", 0.1)
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    async with anyio.create_task_group() as tg:
        _start(tg, election, budget=0.2)
        with anyio.fail_after(5.0):
            await local_in.send(_request("tools/list", "list-1"))
            reply = await local_out.receive()
        assert isinstance(reply.message, JSONRPCError)
        assert reply.message.id == "list-1"
        tg.cancel_scope.cancel()


class _InlineLeader:
    """Stands in for ``_run_leader(stdio_streams=...)``: answers what it is sent."""

    def __init__(self) -> None:
        self.received: list[Any] = []
        self.read_closed = anyio.Event()
        self.finish = anyio.Event()

    async def __call__(self, streams: tuple[Any, Any]) -> None:
        read_stream, write_stream = streams
        async for message in read_stream:
            root = message.message
            self.received.append(root)
            if isinstance(root, JSONRPCRequest):
                await write_stream.send(
                    SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=root.id, result={"answered": root.method}))
                )
        # stdio closed: an inline leader that is discoverable keeps serving HTTP.
        self.read_closed.set()
        await self.finish.wait()


@pytest.mark.anyio
async def test_an_inline_fallback_is_served_over_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    inline = _InlineLeader()
    async with anyio.create_task_group() as tg:
        _start(tg, election)
        with anyio.fail_after(5.0):
            await local_in.send(_initialize())
            await local_out.receive()
            await local_in.send(_initialized())
            await local_in.send(_request("tools/list", "list-1"))
            election.decide(inline)
            reply = await local_out.receive()
        assert reply.message.id == "list-1"
        assert reply.message.result == {"answered": "tools/list"}
        methods = [getattr(root, "method", None) for root in inline.received]
        assert methods == ["initialize", "notifications/initialized", "tools/list"]
        assert leader.sessions == []  # no HTTP connect for an inline leader
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_stdin_eof_closes_the_inline_leaders_stdio_without_stopping_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)
    election = _Election()
    inline = _InlineLeader()
    armed: list[float] = []
    monkeypatch.setattr(runtime, "_arm_follower_exit_backstop", lambda grace, **_k: armed.append(grace))
    done = anyio.Event()

    async def _run() -> None:
        await runtime.run_supervised_proxy(leader_source=election, pre_leader_budget=30.0)
        done.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        with anyio.fail_after(5.0):
            election.decide(inline)
            await local_in.send(_request("tools/list", "list-1"))
            await local_out.receive()
            await local_in.aclose()
            await inline.read_closed.wait()
            await anyio.sleep(0.1)
            assert not done.is_set()  # the leader is still serving
            assert armed == []  # and nothing will hard-exit this process under it
            inline.finish.set()
            await done.wait()
