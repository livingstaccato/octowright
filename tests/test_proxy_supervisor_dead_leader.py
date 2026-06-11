# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse

from octowright import proxy_runtime as supervisor


class FailingClient:
    async def __aenter__(self) -> Any:
        raise ConnectionError("leader is gone")

    async def __aexit__(self, *_exc_info: object) -> None:
        return None


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _request(method: str, request_id: str = "r1") -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params={"x": 1}))
    )


def _response(request_id: str = "r1") -> SessionMessage:
    return SessionMessage(JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=request_id, result={"ok": True})))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_leader_health_alive_returns_true_for_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc_info: object) -> None:
            return None

        async def get(self, _url: str) -> FakeResponse:
            return FakeResponse(200)

    monkeypatch.setattr(supervisor.httpx, "AsyncClient", FakeClient)

    assert await supervisor.leader_health_alive("http://leader.invalid/api/health") is True


@pytest.mark.anyio
async def test_leader_health_alive_returns_false_for_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FailingClient:
            return self

        async def __aexit__(self, *_exc_info: object) -> None:
            return None

        async def get(self, _url: str) -> FakeResponse:
            raise supervisor.httpx.ConnectError("down")

    monkeypatch.setattr(supervisor.httpx, "AsyncClient", FailingClient)

    assert await supervisor.leader_health_alive("http://leader.invalid/api/health") is False


@pytest.mark.anyio
async def test_health_monitor_calls_failure_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status_code = 503

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc_info: object) -> None:
            return None

        async def get(self, _url: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(supervisor.httpx, "AsyncClient", FakeClient)
    failed: list[bool] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            supervisor.monitor_leader_health,
            tg.cancel_scope,
            "http://leader.invalid/api/health",
            0.01,
            2,
            lambda: failed.append(True),
        )
        with anyio.move_on_after(1.0):
            await anyio.sleep_forever()

    assert failed == [True]


@pytest.mark.anyio
async def test_run_supervised_proxy_exits_when_connect_and_health_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead leader should unwind the follower so serve.py can respawn it."""

    def failing_client(_url: str, **_kwargs: Any) -> FailingClient:
        return FailingClient()

    monkeypatch.setattr(supervisor, "streamablehttp_client", failing_client)
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    async def health_is_dead(_url: str) -> bool:
        return False

    monkeypatch.setattr(supervisor, "leader_health_alive", health_is_dead)

    with anyio.fail_after(2.0), pytest.raises(RuntimeError, match="leader health check failed"):
        await supervisor.run_supervised_proxy(
            leader_mcp_url="http://leader.invalid/mcp/",
            health_url="http://leader.invalid/api/health",
        )

    await local_in_send.aclose()


@pytest.mark.anyio
async def test_request_timeout_recycles_remote_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timed-out leader RPC should force a fresh HTTP-MCP session."""

    monkeypatch.setattr(supervisor, "BRIDGE_REQUEST_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)

    enters: list[int] = []
    remote_sends: list[anyio.abc.ObjectSendStream[SessionMessage]] = []

    @asynccontextmanager
    async def silent_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        enters.append(len(enters) + 1)
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_write_send, _remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_sends.append(remote_read_send)
        try:
            yield (remote_read_recv, remote_write_send, lambda: f"sess-{len(enters)}")
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(supervisor, "streamablehttp_client", silent_client)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    async with anyio.create_task_group() as tg:

        async def run_proxy() -> None:
            await supervisor.run_supervised_proxy(leader_mcp_url="http://leader.invalid/mcp/")

        tg.start_soon(run_proxy)
        await local_in_send.send(_request("tools/call", "timeout-1"))
        err = await local_out_recv.receive()
        assert "timed out while waiting for leader response" in err.message.root.error.message
        await anyio.sleep(0.1)
        assert len(enters) >= 2
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_clean_remote_stream_end_exits_when_health_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """A leader stream may close without raising; dead health still exits the follower."""

    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)

    @asynccontextmanager
    async def closing_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_write_send, _remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        await remote_read_send.aclose()
        yield (remote_read_recv, remote_write_send, lambda: "sess-clean-close")

    monkeypatch.setattr(supervisor, "streamablehttp_client", closing_client)

    async def health_is_dead(_url: str) -> bool:
        return False

    monkeypatch.setattr(supervisor, "leader_health_alive", health_is_dead)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    with anyio.fail_after(0.5), pytest.raises(RuntimeError, match="leader health check failed"):
        await supervisor.run_supervised_proxy(
            leader_mcp_url="http://leader.invalid/mcp/",
            health_url="http://leader.invalid/api/health",
        )

    await local_in_send.aclose()


@pytest.mark.anyio
async def test_remote_reader_forwards_messages_and_handles_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)

    remote_read_sends: list[anyio.abc.ObjectSendStream[Any]] = []
    remote_write_recvs: list[anyio.abc.ObjectReceiveStream[SessionMessage]] = []

    @asynccontextmanager
    async def controllable_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[Any](10)
        remote_write_send, remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_read_sends.append(remote_read_send)
        remote_write_recvs.append(remote_write_recv)
        try:
            yield (remote_read_recv, remote_write_send, lambda: "sess-controlled")
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(supervisor, "streamablehttp_client", controllable_client)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    async def run_proxy() -> None:
        await supervisor.run_supervised_proxy(leader_mcp_url="http://leader.invalid/mcp/")

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_proxy)
        while not remote_read_sends:
            await anyio.sleep(0)

        await local_in_send.send(_request("tools/call", "ok-1"))
        await remote_write_recvs[0].receive()
        await remote_read_sends[0].send(_response("ok-1"))
        assert await local_out_recv.receive() == _response("ok-1")

        await remote_read_sends[0].send(RuntimeError("remote stream failed"))
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()
