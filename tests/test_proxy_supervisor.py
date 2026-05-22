# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from octowright import proxy_supervisor as supervisor


def _request(method: str, request_id: str = "r1") -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params={"x": 1}))
    )


def _notification(method: str) -> SessionMessage:
    return SessionMessage(JSONRPCMessage(root=JSONRPCNotification(jsonrpc="2.0", method=method, params={"x": 1})))


def _response(request_id: str = "r1") -> SessionMessage:
    return SessionMessage(JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=request_id, result={"ok": True})))


class FailingRemoteWrite:
    async def send(self, _message: SessionMessage) -> None:
        raise anyio.ClosedResourceError


def test_request_id_and_method_for_request() -> None:
    msg = _request("tools/call", "abc")
    assert supervisor.message_request_id(msg) == "abc"
    assert supervisor.message_method(msg) == "tools/call"
    assert supervisor.is_request(msg) is True
    assert supervisor.is_response(msg) is False


def test_request_id_for_response() -> None:
    msg = _response("abc")
    assert supervisor.message_request_id(msg) == "abc"
    assert supervisor.message_method(msg) is None
    assert supervisor.is_request(msg) is False
    assert supervisor.is_response(msg) is True


def test_notification_has_method_but_no_request_id() -> None:
    msg = _notification("notifications/initialized")
    assert supervisor.message_request_id(msg) is None
    assert supervisor.message_method(msg) == "notifications/initialized"
    assert supervisor.is_request(msg) is False


def test_bridge_error_message_shape() -> None:
    error = supervisor.bridge_error("abc", "remote request timed out")
    root = error.message.root
    assert isinstance(root, JSONRPCError)
    assert root.id == "abc"
    assert root.error.code == -32000
    assert root.error.message == "Octowright bridge error: remote request timed out"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_request_timeout_returns_bridge_error() -> None:
    local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=0.05,
    )

    await local_send.send(_request("tools/call", "timeout-id"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.forward_local_to_remote, remote_send)
        tg.start_soon(supervisor_obj.watch_deadlines)
        forwarded = await remote_recv.receive()
        assert supervisor.message_request_id(forwarded) == "timeout-id"
        error = await outgoing_recv.receive()
        root = error.message.root
        assert isinstance(root, JSONRPCError)
        assert root.id == "timeout-id"
        assert "timed out" in root.error.message
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_remote_response_clears_in_flight() -> None:
    local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_write_send, remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    await local_send.send(_request("tools/call", "ok-id"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.forward_local_to_remote, remote_write_send)
        forwarded = await remote_write_recv.receive()
        assert supervisor.message_request_id(forwarded) == "ok-id"
        await supervisor_obj.forward_remote_message(_response("ok-id"))
        response = await outgoing_recv.receive()
        assert supervisor.message_request_id(response) == "ok-id"
        assert supervisor_obj.in_flight_count == 0
        tg.cancel_scope.cancel()


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
    local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    connector = FakeRemoteConnector()
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )

    await local_send.send(_request("initialize", "init-1"))
    _remote_read, remote_write, _sid = await connector.connect()
    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.forward_local_to_remote, remote_write)
        first_remote_recv, _first_remote_send = connector.sessions[0]
        init_msg = await first_remote_recv.receive()
        assert supervisor.message_method(init_msg) == "initialize"
        await supervisor_obj.forward_remote_message(_response("init-1"))
        assert supervisor.message_request_id(await local_out_recv.receive()) == "init-1"
        await supervisor_obj.replay_initialize(remote_write)
        replayed = await first_remote_recv.receive()
        assert supervisor.message_method(replayed) == "initialize"
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_remote_failure_fails_in_flight() -> None:
    local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_write_send, remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )

    await local_send.send(_request("tools/call", "lost-id"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.forward_local_to_remote, remote_write_send)
        assert supervisor.message_request_id(await remote_write_recv.receive()) == "lost-id"
        await supervisor_obj.fail_all_in_flight("remote leader stream closed")
        error = await local_out_recv.receive()
        root = error.message.root
        assert isinstance(root, JSONRPCError)
        assert root.id == "lost-id"
        assert "remote leader stream closed" in root.error.message
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_forward_one_local_message_drops_stale_remote_writer_and_fails_request() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=local_out_send,
        request_timeout_seconds=1.0,
    )
    remote_write_box: dict[str, Any] = {"remote_write": FailingRemoteWrite()}

    await supervisor_obj.forward_one_local_message(_request("tools/call", "stale-id"), remote_write_box)

    assert "remote_write" not in remote_write_box
    error = await local_out_recv.receive()
    root = error.message.root
    assert isinstance(root, JSONRPCError)
    assert root.id == "stale-id"
    assert "leader session unavailable" in root.error.message


@pytest.mark.anyio
async def test_health_monitor_cancels_remote_scope_after_consecutive_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

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

    monkeypatch.setattr(supervisor.httpx, "AsyncClient", FakeClient)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            supervisor.monitor_leader_health,
            tg.cancel_scope,
            "http://leader/api/health",
            0.01,
            2,
        )
        with anyio.move_on_after(1.0):
            await anyio.sleep_forever()

    assert calls >= 2


def test_backoff_sequence_caps_at_max() -> None:
    assert [supervisor.reconnect_delay(i, max_delay=5.0) for i in range(6)] == [
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        5.0,
    ]
