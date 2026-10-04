# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""An in-flight call is only re-sent to the leader it was first sent to.

Resume is safe because the leader dedups a re-sent call on its idempotency key
-- but that cache lives in the leader process. When the leader is REPLACED
(killed, restarted, respawned) inside the follower's recovery window, the new
daemon has never seen the key, so re-sending a call whose response was lost
with the old leader executed its side effect a second time. A call whose leader
changed is answered with an explicit unknown-outcome error instead.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError

from octowright import proxy_runtime as runtime
from octowright import proxy_supervisor as supervisor
from octowright import singleton
from tests._proxy_supervisor_helpers import _tools_call


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _bridge() -> tuple[supervisor.BridgeSupervisor, Any]:
    _in_send, in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    bridge = supervisor.BridgeSupervisor(local_read=in_recv, local_write=out_send, request_timeout_seconds=20.0)
    return bridge, out_recv


@pytest.mark.anyio
async def test_a_call_is_not_resent_to_a_replacement_leader() -> None:
    bridge, out_recv = _bridge()
    remote1_send, remote1_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote2_send, remote2_recv = anyio.create_memory_object_stream[SessionMessage](10)

    bridge.leader_generation = "leader-A"
    await bridge.forward_one_local_message(
        _tools_call("persona_create", "p1"), supervisor._RemoteWriteSlot(remote1_send)
    )
    await remote1_recv.receive()
    await bridge.fail_or_mark_for_resume("remote leader session reset")

    bridge.leader_generation = "leader-B"
    await bridge.resume_in_flight(remote2_send)

    with pytest.raises(anyio.WouldBlock):
        remote2_recv.receive_nowait()
    err = out_recv.receive_nowait()
    assert isinstance(err.message, JSONRPCError)
    assert err.message.id == "p1"
    assert "outcome is unknown" in err.message.error.message
    assert "p1" not in bridge._in_flight


@pytest.mark.anyio
async def test_a_call_is_resent_to_the_same_leader() -> None:
    bridge, out_recv = _bridge()
    remote1_send, remote1_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote2_send, remote2_recv = anyio.create_memory_object_stream[SessionMessage](10)

    bridge.leader_generation = "leader-A"
    await bridge.forward_one_local_message(
        _tools_call("persona_create", "p1"), supervisor._RemoteWriteSlot(remote1_send)
    )
    await remote1_recv.receive()
    await bridge.fail_or_mark_for_resume("remote leader session reset")

    await bridge.resume_in_flight(remote2_send)

    assert (await remote2_recv.receive()).message.id == "p1"
    with pytest.raises(anyio.WouldBlock):
        out_recv.receive_nowait()


def test_generation_comes_from_the_live_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / "octowright.lock"
    info = singleton.make_leader_info("127.0.0.1", 6299, token="t")
    singleton.write_lock(info, path=lock)
    monkeypatch.setattr(singleton, "read_lock", lambda: singleton.LeaderInfo.from_json(lock.read_text()))

    first = runtime.resolve_leader_generation()
    assert first is not None

    singleton.write_lock(singleton.LeaderInfo(**{**info.__dict__, "started_at": info.started_at + 5}), path=lock)
    assert runtime.resolve_leader_generation() not in (None, first)


def test_no_live_lock_has_no_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(singleton, "read_lock", lambda: None)
    assert runtime.resolve_leader_generation() is None


@pytest.mark.anyio
async def test_the_runtime_stamps_each_connection_with_the_leader_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end through the reconnect loop: the call sent to leader A and lost
    is not replayed on the session to leader B."""
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(runtime, "resolve_leader_token", lambda: "")
    monkeypatch.setattr(runtime.bridge_state, "record_snapshot", lambda **_kwargs: True)
    monkeypatch.setattr(runtime, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)

    async def _no_notifications(*_a: Any, **_k: Any) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr(runtime, "consume_leader_notifications", _no_notifications)
    connects = {"n": 0}

    def _generation() -> str:
        connects["n"] += 1
        return "leader-A" if connects["n"] == 1 else "leader-B"

    monkeypatch.setattr(runtime, "resolve_leader_generation", _generation)

    sessions: list[tuple[Any, Any]] = []

    @asynccontextmanager
    async def client(_url: str, **_k: Any):  # type: ignore[no-untyped-def]
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[Any](10)
        remote_write_send, remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        sessions.append((remote_read_send, remote_write_recv))
        try:
            yield (remote_read_recv, remote_write_send)
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(runtime, "streamable_http_client", client)
    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)

    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: runtime.run_supervised_proxy(leader_mcp_url="http://127.0.0.1:6299/mcp/"))
        with anyio.fail_after(3.0):
            while not sessions:
                await anyio.sleep(0.01)
            await local_in_send.send(_tools_call("persona_create", "p1"))
            assert (await sessions[0][1].receive()).message.id == "p1"
            await sessions[0][0].aclose()  # leader A's stream drops with the call unanswered
            err = await local_out_recv.receive()
        assert isinstance(err.message, JSONRPCError)
        assert "outcome is unknown" in err.message.error.message
        assert len(sessions) >= 2
        with pytest.raises(anyio.WouldBlock):
            sessions[1][1].receive_nowait()
        tg.cancel_scope.cancel()
    await local_in_send.aclose()


def test_the_unknown_outcome_answer_names_the_product_in_prose() -> None:
    """The reason reaches the agent and the user verbatim: product name in
    prose is "Octowright", not the lowercase identifier."""
    assert "Octowright leader" in supervisor.LEADER_REPLACED_REASON
    assert "octowright leader" not in supervisor.LEADER_REPLACED_REASON
