# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""How the follower's reconnect loop opens and closes a leader session.

Two orderings, both driven end to end through ``run_supervised_proxy`` with an
in-memory leader:

* **A reconnect publishes the writer only once the session is initialized.** It
  used to publish it and wake queued calls, then await a bridge-state snapshot
  before replaying ``initialize``, so a call waiting out the reconnect went to
  the new leader first. The leader answers a call on an uninitialized session
  with an error (and opens a stray session against the follower's new-session
  rate limit), which the agent saw as bad parameters during an ordinary restart.
* **A session that ends cleanly is cleaned up like one that ends with an
  error.** Only the exception path cleared the writer and failed or kept the
  in-flight calls; a leader closing its stream cleanly (graceful restart, a
  reaped session) left the closed writer published, so calls failed instead of
  waiting, and a call in flight on it was answered only when its deadline
  expired -- and that expiry then cancelled whichever session was current.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError

from octowright import proxy_runtime as runtime
from tests._proxy_supervisor_helpers import _request, _tools_call


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Leader:
    """Hands out one in-memory session per connect and records what each got."""

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
            # As the real transport does: its streams close with the session.
            await read_send.aclose()
            await write_send.aclose()

    async def wait_for(self, count: int) -> None:
        while len(self.sessions) < count:
            await anyio.sleep(0.01)


def _wire(monkeypatch: pytest.MonkeyPatch, leader: _Leader) -> tuple[Any, Any]:
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(runtime, "resolve_leader_token", lambda: "")
    monkeypatch.setattr(runtime, "resolve_leader_generation", lambda: "leader-A")
    monkeypatch.setattr(runtime, "streamable_http_client", leader.client)

    async def _no_notifications(*_a: Any, **_k: Any) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr(runtime, "consume_leader_notifications", _no_notifications)

    async def _no_flap_backoff(_connected_at: float | None, flap_attempt: int) -> tuple[int, bool]:
        return flap_attempt, True

    monkeypatch.setattr(runtime, "_flap_backoff", _no_flap_backoff)
    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](20)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](20)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)
    return local_in_send, local_out_recv


@pytest.mark.anyio
async def test_a_queued_call_goes_to_the_new_leader_after_initialize(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, _local_out = _wire(monkeypatch, leader)
    snapshots = {"n": 0}
    reconnect_snapshot = anyio.Event()
    release = anyio.Event()

    async def _snapshot(**_k: Any) -> bool:
        snapshots["n"] += 1
        if snapshots["n"] == 3:  # connect 1, the reset, then connect 2
            reconnect_snapshot.set()
            await release.wait()
        return True

    monkeypatch.setattr(runtime.bridge_state, "record_snapshot_async", _snapshot)

    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url="http://127.0.0.1:6299/mcp/"))
        with anyio.fail_after(5.0):
            await leader.wait_for(1)
            await local_in.send(_request("initialize", "init-1"))
            assert (await leader.sessions[0][1].receive()).message.id == "init-1"
            await leader.sessions[0][0].send(RuntimeError("leader dropped the stream"))
            await leader.wait_for(2)
            await reconnect_snapshot.wait()
            await local_in.send(_tools_call("persona_list", "call-1"))
            await anyio.sleep(0.1)  # let the forwarder act on whatever is published
            release.set()
            first = await leader.sessions[1][1].receive()
            second = await leader.sessions[1][1].receive()
        assert str(first.message.id).startswith("octowright-bridge-replay"), first
        assert second.message.id == "call-1"
        tg.cancel_scope.cancel()
    await local_in.aclose()


@pytest.mark.anyio
async def test_a_clean_session_end_fails_the_calls_it_cannot_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    leader = _Leader()
    local_in, local_out = _wire(monkeypatch, leader)

    async def _snapshot(**_k: Any) -> bool:
        return True

    monkeypatch.setattr(runtime.bridge_state, "record_snapshot_async", _snapshot)

    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url="http://127.0.0.1:6299/mcp/"))
        with anyio.fail_after(5.0):
            await leader.wait_for(1)
            # Not a tools/call: no idempotency key, so it cannot be re-sent.
            await local_in.send(_request("resources/list", "r1"))
            assert (await leader.sessions[0][1].receive()).message.id == "r1"
            await leader.sessions[0][0].aclose()  # the leader ends the stream cleanly
            answer = await local_out.receive()
        assert isinstance(answer.message, JSONRPCError)
        assert answer.message.id == "r1"
        assert "remote leader session" in answer.message.error.message
        tg.cancel_scope.cancel()
    await local_in.aclose()


@pytest.mark.anyio
async def test_a_clean_session_end_unpublishes_its_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A call made between a clean end and the next connect must wait for that
    connect, not be written into the closed session."""
    leader = _Leader()
    local_in, _local_out = _wire(monkeypatch, leader)
    hold = anyio.Event()

    async def _snapshot(**_k: Any) -> bool:
        return True

    async def _held_flap_backoff(_connected_at: float | None, flap_attempt: int) -> tuple[int, bool]:
        await hold.wait()
        return flap_attempt, True

    monkeypatch.setattr(runtime.bridge_state, "record_snapshot_async", _snapshot)
    monkeypatch.setattr(runtime, "_flap_backoff", _held_flap_backoff)

    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url="http://127.0.0.1:6299/mcp/"))
        with anyio.fail_after(5.0):
            await leader.wait_for(1)
            await leader.sessions[0][0].aclose()
            await anyio.sleep(0.1)  # the loop is now holding in its post-session backoff
            # Not resumable, so only waiting for the next session can deliver it.
            await local_in.send(_request("resources/list", "r2"))
            await anyio.sleep(0.1)
            hold.set()
            await leader.wait_for(2)
            first = await leader.sessions[1][1].receive()
        assert first.message.id == "r2"
        tg.cancel_scope.cancel()
    await local_in.aclose()
