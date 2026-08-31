# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError

from octowright import proxy_runtime as runtime
from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import (
    FailingRemoteWrite,
    _request,
    _response,
    _tools_call,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_kill_switch_fails_instead_of_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """With OCTOWRIGHT_IDEMPOTENCY off, a tools/call is failed on reset (today's
    fail-safe), not resumed."""
    monkeypatch.setattr(supervisor.defaults, "IDEMPOTENCY_ENABLED", False)
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    remote_send, _remote_recv = anyio.create_memory_object_stream[SessionMessage](10)

    await sup.forward_one_local_message(_tools_call("browser_click", "c1"), supervisor._RemoteWriteSlot(remote_send))
    await sup.fail_or_mark_for_resume("reset")

    err = outgoing_recv.receive_nowait()
    assert supervisor.message_request_id(err) == "c1"
    assert "c1" not in sup._in_flight


@pytest.mark.anyio
async def test_kill_switch_omits_idempotency_key_but_keeps_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """With idempotency off, the bridge injects no idempotency key (today's exact
    wire format) but still injects a progressToken — deadline extension is
    independent of idempotency."""
    monkeypatch.setattr(supervisor.defaults, "IDEMPOTENCY_ENABLED", False)
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)

    await sup.forward_one_local_message(_tools_call("browser_launch", "bl1"), supervisor._RemoteWriteSlot(remote_send))

    sent = await remote_recv.receive()
    meta = sent.message.params.get("_meta", {})
    assert "octowrightIdempotencyKey" not in meta
    assert meta.get("progressToken")
    assert sup._in_flight["bl1"].idempotency_key is None


class FakeRemoteConnector:
    def __init__(self) -> None:
        self.sessions: list[tuple[Any, Any]] = []
        self.connect_count = 0

    async def connect(self) -> tuple[Any, Any, str | None]:
        self.connect_count += 1
        client_to_remote_send, client_to_remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_to_client_send, remote_to_client_recv = anyio.create_memory_object_stream[SessionMessage](10)
        self.sessions.append((client_to_remote_recv, remote_to_client_send))
        return remote_to_client_recv, client_to_remote_send, f"session-{self.connect_count}"


@pytest.mark.anyio
async def test_initialize_is_replayed_after_reconnect() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    connector = FakeRemoteConnector()
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )

    # Simulate the supervisor's per-message forwarding for the initial
    # `initialize` request — track it and push it on the first remote write.
    init_request = _request("initialize", "init-1")
    supervisor_obj.track_local_message(init_request)
    _remote_read, remote_write, _sid = await connector.connect()
    await remote_write.send(init_request)

    first_remote_recv, _first_remote_send = connector.sessions[0]
    init_msg = await first_remote_recv.receive()
    assert supervisor.message_method(init_msg) == "initialize"
    await supervisor_obj.forward_remote_message(_response("init-1"))
    assert supervisor.message_request_id(await local_out_recv.receive()) == "init-1"
    await supervisor_obj.replay_initialize(remote_write)
    replayed = await first_remote_recv.receive()
    assert supervisor.message_method(replayed) == "initialize"


@pytest.mark.anyio
async def test_replay_initialize_uses_fresh_request_id_on_each_replay() -> None:
    """Each reconnect-driven replay must mint a new request id so the leader
    never sees a duplicate id within one bridge lifetime, even though the
    client-visible initialize already completed with the original id."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    connector = FakeRemoteConnector()
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )

    init_request = _request("initialize", "client-init-id")
    supervisor_obj.track_local_message(init_request)

    _read1, remote_write1, _sid1 = await connector.connect()
    await supervisor_obj.replay_initialize(remote_write1)
    first_seen = await connector.sessions[0][0].receive()
    first_id = supervisor.message_request_id(first_seen)

    _read2, remote_write2, _sid2 = await connector.connect()
    await supervisor_obj.replay_initialize(remote_write2)
    second_seen = await connector.sessions[1][0].receive()
    second_id = supervisor.message_request_id(second_seen)

    assert first_id != "client-init-id"
    assert second_id != "client-init-id"
    assert first_id != second_id


@pytest.mark.anyio
async def test_replay_initialize_response_is_swallowed_not_forwarded() -> None:
    """The leader's response to a replay must not reach the local client —
    the client got its initialize response on the very first attempt and a
    second one with the same client id is a protocol violation."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    connector = FakeRemoteConnector()
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )

    init_request = _request("initialize", "client-init-id")
    supervisor_obj.track_local_message(init_request)

    _read, remote_write, _sid = await connector.connect()
    await supervisor_obj.replay_initialize(remote_write)
    seen_on_wire = await connector.sessions[0][0].receive()
    replay_id = supervisor.message_request_id(seen_on_wire)
    assert isinstance(replay_id, str)

    # Leader answers using the replay id.
    await supervisor_obj.forward_remote_message(_response(replay_id))

    with anyio.move_on_after(0.05):
        leaked = await local_out_recv.receive()
        raise AssertionError(f"replay response leaked to local client: {leaked!r}")


@pytest.mark.anyio
async def test_remote_failure_fails_in_flight() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )

    supervisor_obj.track_local_message(_request("tools/call", "lost-id"))
    assert supervisor_obj.in_flight_count == 1

    await supervisor_obj.fail_all_in_flight("remote leader stream closed")
    error = await local_out_recv.receive()
    root = error.message
    assert isinstance(root, JSONRPCError)
    assert root.id == "lost-id"
    assert "remote leader stream closed" in root.error.message
    assert supervisor_obj.in_flight_count == 0


@pytest.mark.anyio
async def test_forward_one_local_message_drops_stale_remote_writer_and_fails_request() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )
    remote_write_slot = supervisor._RemoteWriteSlot(write=FailingRemoteWrite())

    await supervisor_obj.forward_one_local_message(_request("tools/call", "stale-id"), remote_write_slot)

    assert remote_write_slot.write is None
    error = await local_out_recv.receive()
    root = error.message
    assert isinstance(root, JSONRPCError)
    assert root.id == "stale-id"
    assert "leader session unavailable" in root.error.message


@pytest.mark.anyio
async def test_health_monitor_unsticks_after_consecutive_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """After max_failures consecutive health misses the monitor calls on_unhealthy
    (which unsticks the stuck connection) and KEEPS running — it never tears the
    follower down itself."""
    calls = 0
    unstuck: list[bool] = []

    class FakeResponse:
        status_code = 503

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> FakeResponse:
            nonlocal calls
            calls += 1
            return FakeResponse()

    monkeypatch.setattr(runtime.httpx2, "AsyncClient", FakeClient)

    async with anyio.create_task_group() as tg:
        tg.start_soon(runtime.monitor_leader_health, "http://leader/api/health", 0.01, 2, lambda: unstuck.append(True))
        # Poll rather than sleep: `unstuck` is set only after two consecutive
        # failed health calls, so a fixed span races the monitor under load.
        with anyio.move_on_after(5.0):
            while not unstuck:
                await anyio.sleep(0.01)
        tg.cancel_scope.cancel()  # monitor loops forever; we stop it

    assert calls >= 2
    assert unstuck  # on_unhealthy fired (and would fire again — the monitor kept watching)


def test_backoff_sequence_caps_at_max() -> None:
    assert [runtime.reconnect_delay(i, max_delay=5.0) for i in range(6)] == [
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        5.0,
    ]


def test_post_session_backoff_throttles_flaps_and_resets_on_healthy() -> None:
    """A cleanly-ended session shorter than BRIDGE_MIN_SESSION_SECONDS is a flap:
    it must return a positive backoff and increment the flap counter, so the
    success-path reconnect can't hot-loop the leader. A healthy-length session
    returns 0 delay and resets the counter."""
    short = runtime.BRIDGE_MIN_SESSION_SECONDS / 10.0

    # Consecutive flaps → increasing backoff (matches reconnect_delay(1..4)) with a
    # monotonically rising flap counter.
    d1, n1 = runtime._post_session_backoff(short, 0)
    d2, n2 = runtime._post_session_backoff(short, n1)
    d3, n3 = runtime._post_session_backoff(short, n2)
    d4, n4 = runtime._post_session_backoff(short, n3)
    d5, n5 = runtime._post_session_backoff(short, n4)
    assert (n1, n2, n3, n4, n5) == (1, 2, 3, 4, 5)
    assert [d1, d2, d3, d4] == [0.5, 1.0, 2.0, runtime.BRIDGE_RECONNECT_MAX_SECONDS]
    assert d5 == runtime.BRIDGE_RECONNECT_MAX_SECONDS  # caps at max
    assert d1 > 0  # a flap always backs off (never zero-delay hot-loop)

    # A session that lived long enough is NOT a flap → no delay, counter resets.
    delay, flaps = runtime._post_session_backoff(runtime.BRIDGE_MIN_SESSION_SECONDS + 0.01, 4)
    assert delay == 0.0
    assert flaps == 0

    # Boundary: exactly the threshold counts as healthy (not a flap).
    assert runtime._post_session_backoff(runtime.BRIDGE_MIN_SESSION_SECONDS, 3) == (0.0, 0)


def test_within_recovery_window() -> None:
    # Not yet stamped → treat as inside the window.
    assert runtime._within_recovery_window(None, 100.0, 15.0) is True
    # Stamped, still inside.
    assert runtime._within_recovery_window(100.0, 110.0, 15.0) is True
    # Stamped, window elapsed → give up.
    assert runtime._within_recovery_window(100.0, 116.0, 15.0) is False
    # Window 0 = no grace = legacy immediate-exit behavior.
    assert runtime._within_recovery_window(100.0, 100.0, 0.0) is False


def test_default_recovery_window_survives_a_normal_restart() -> None:
    """The default must outlast a real leader restart, or every client's MCP
    session dies on every `octowright restart` (SIGTERM grace + wait-for-port-free
    + spawn + health routinely exceeds 20-30s). Guards against regressing to the
    old 15s default that broke octowright across all clients on restart."""
    default = runtime.BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS
    assert default >= 120.0, f"recovery window {default}s is too short to survive a leader restart"
    # A 30s and a 60s outage (well within a restart's span) must keep retrying, not give up.
    assert runtime._within_recovery_window(100.0, 130.0, default) is True
    assert runtime._within_recovery_window(100.0, 160.0, default) is True
