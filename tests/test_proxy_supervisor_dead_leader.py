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
from mcp.types import JSONRPCRequest, JSONRPCResponse

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
    return SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params={"x": 1}))


def _response(request_id: str = "r1") -> SessionMessage:
    return SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request_id, result={"ok": True}))


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

    monkeypatch.setattr(supervisor.httpx2, "AsyncClient", FakeClient)

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
            raise supervisor.httpx2.ConnectError("down")

    monkeypatch.setattr(supervisor.httpx2, "AsyncClient", FailingClient)

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

    monkeypatch.setattr(supervisor.httpx2, "AsyncClient", FakeClient)
    unhealthy: list[bool] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            supervisor.monitor_leader_health,
            "http://leader.invalid/api/health",
            0.01,
            2,
            lambda: unhealthy.append(True),
        )
        # Two consecutive health failures at a 0.01s interval -- fast in
        # principle, unbounded under CI load. Same race as above, not yet
        # observed failing.
        with anyio.move_on_after(5.0):
            while not unhealthy:
                await anyio.sleep(0.01)
        tg.cancel_scope.cancel()  # monitor loops forever now; stop it explicitly

    assert unhealthy  # on_unhealthy fired after the consecutive failures


@pytest.mark.anyio
async def test_run_supervised_proxy_exits_when_connect_and_health_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead leader should unwind the follower so serve.py can respawn it."""

    def failing_client(_url: str, **_kwargs: Any) -> FailingClient:
        return FailingClient()

    monkeypatch.setattr(supervisor, "streamable_http_client", failing_client)
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)
    # Window 0 = no recovery grace = legacy immediate-exit when the leader is gone.
    monkeypatch.setattr(supervisor, "BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", 0.0)

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
    # The timed-out session dies ~30ms after opening, so the success-path flap
    # guard throttles the recycle; keep that backoff tiny so the recycle still
    # lands inside the test's wait window (the guard itself is covered by
    # test_flapping_session_is_throttled_not_hot_looped).
    monkeypatch.setattr(supervisor, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)

    enters: list[int] = []
    remote_sends: list[anyio.abc.ObjectSendStream[SessionMessage]] = []

    @asynccontextmanager
    async def silent_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        enters.append(len(enters) + 1)
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_write_send, _remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_sends.append(remote_read_send)
        try:
            yield (remote_read_recv, remote_write_send)
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(supervisor, "streamable_http_client", silent_client)

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
        assert "timed out while waiting for leader response" in err.message.error.message
        # Poll for the recycle instead of sleeping a fixed span. It needs the
        # 0.03s request timeout plus the 0.01s flap backoff to elapse and then a
        # fresh client enter, and a loaded runner can take far longer than any
        # constant chosen here -- this assertion failed on windows-arm64 with
        # enters still at 1. move_on_after rather than fail_after so a genuine
        # regression reports the observed value rather than a bare TimeoutError.
        with anyio.move_on_after(5.0):
            while len(enters) < 2:
                await anyio.sleep(0.01)
        assert len(enters) >= 2, f"session was not recycled: enters={enters}"
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
        yield (remote_read_recv, remote_write_send)

    monkeypatch.setattr(supervisor, "streamable_http_client", closing_client)
    monkeypatch.setattr(supervisor, "BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", 0.0)

    async def health_is_dead(_url: str) -> bool:
        return False

    monkeypatch.setattr(supervisor, "leader_health_alive", health_is_dead)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    # 3.0s to match every other assertion in this file. This is a hang guard, not
    # a performance assertion -- the test asserts that a dead leader RAISES, not
    # that it raises quickly -- and at 0.5s it was the lone sub-second outlier
    # here. Windows arm64 CI blew that budget and reported it as a bare
    # TimeoutError ("Cancelled via cancel scope ...; reason: deadline exceeded"),
    # while the same test failed 0 times in 40 local runs.
    with anyio.fail_after(3.0), pytest.raises(RuntimeError, match="leader health check failed"):
        await supervisor.run_supervised_proxy(
            leader_mcp_url="http://leader.invalid/mcp/",
            health_url="http://leader.invalid/api/health",
        )

    await local_in_send.aclose()


@pytest.mark.anyio
async def test_survives_leader_bounce_within_recovery_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESSION: a leader that drops then returns within the recovery window
    (an `octowright restart` / respawn) must NOT kill the follower — it retries
    through the gap and reconnects to the new leader, keeping the client session."""
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)
    monkeypatch.setattr(supervisor, "BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", 5.0)
    from tests._metric_recorders import RecordingCounter

    recovery = RecordingCounter()
    monkeypatch.setattr(supervisor, "_LEADER_RECOVERY", recovery)

    # Health is dead for the first two probes (the restart gap), then alive.
    health_calls = {"n": 0}

    async def health(_url: str) -> bool:
        health_calls["n"] += 1
        return health_calls["n"] > 2

    monkeypatch.setattr(supervisor, "leader_health_alive", health)

    connect_attempts = {"n": 0}
    remote_write_recvs: list[anyio.abc.ObjectReceiveStream[SessionMessage]] = []

    @asynccontextmanager
    async def bouncing_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        connect_attempts["n"] += 1
        if connect_attempts["n"] <= 2:
            raise ConnectionError("leader restarting")
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[Any](10)
        remote_write_send, remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_write_recvs.append(remote_write_recv)
        try:
            yield (remote_read_recv, remote_write_send)
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(supervisor, "streamable_http_client", bouncing_client)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    async def run_proxy() -> None:
        await supervisor.run_supervised_proxy(
            leader_mcp_url="http://leader.invalid/mcp/",
            health_url="http://leader.invalid/api/health",
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_proxy)
        # The follower reconnects (3rd attempt yields a live stream) instead of exiting.
        with anyio.fail_after(2.0):
            while not remote_write_recvs:
                await anyio.sleep(0.01)
        assert connect_attempts["n"] >= 3  # retried through the gap, did not give up
        # And a request now round-trips to the reconnected leader.
        await local_in_send.send(_request("tools/call", "after-bounce"))
        await remote_write_recvs[0].receive()
        # The stream becomes writable before the off-thread bridge-state
        # snapshot and recovery accounting finish.  Await the metric instead
        # of assuming those later steps complete in the same scheduler turn.
        with anyio.fail_after(2.0):
            while recovery.attrs_for("outcome") != ["recovered"]:
                await anyio.sleep(0.01)
        assert recovery.attrs_for("outcome") == ["recovered"]  # the survival was metered
        tg.cancel_scope.cancel()

    await local_in_send.aclose()


@pytest.mark.anyio
async def test_silent_sse_is_unstuck_so_follower_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESSION: a leader whose SSE read goes SILENT (a hard crash — no close,
    no error) must NOT wedge the follower. The health monitor cancels the stuck
    connection so the inline loop reconnects, instead of hanging on a dead socket
    until the recovery window expires (and then giving up)."""
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)
    monkeypatch.setattr(supervisor, "BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", 5.0)

    class _Resp:
        status_code = 503  # leader HTTP is unreachable → monitor fires the unstick

    class _HClient:
        def __init__(self, **_k: Any) -> None: ...

        async def __aenter__(self) -> _HClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def get(self, _u: str) -> _Resp:
            return _Resp()

    monkeypatch.setattr(supervisor.httpx2, "AsyncClient", _HClient)

    connects = {"n": 0}

    @asynccontextmanager
    async def silent_client(_url: str, **_k: Any):  # type: ignore[no-untyped-def]
        # The SSE reader never gets a message and never sees the stream close —
        # it hangs until the monitor cancels the connection's scope.
        connects["n"] += 1
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[Any](10)
        remote_write_send, _ = anyio.create_memory_object_stream[SessionMessage](10)
        try:
            yield (remote_read_recv, remote_write_send)
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(supervisor, "streamable_http_client", silent_client)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    async def run_proxy() -> None:
        await supervisor.run_supervised_proxy(
            leader_mcp_url="http://leader.invalid/mcp/",
            health_url="http://leader.invalid/api/health",
            heartbeat_interval=0.02,
            heartbeat_max_failures=2,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_proxy)
        # First connection is silent; the monitor must unstick it so a SECOND
        # connect happens (proves reconnect, not wedge). On the old code the
        # monitor would tear the follower down at the window instead.
        with anyio.fail_after(3.0):
            while connects["n"] < 2:
                await anyio.sleep(0.02)
        assert connects["n"] >= 2
        tg.cancel_scope.cancel()

    await local_in_send.aclose()


@pytest.mark.anyio
async def test_reconnects_on_transient_drop_while_leader_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stream drop while the leader is still HEALTHY (a transient SSE hiccup):
    the follower reconnects in place and clears the recovery clock, rather than
    counting it against the recovery window."""
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor, "reconnect_delay", lambda _attempt, *, max_delay: 0.01)

    async def health_alive(_url: str) -> bool:
        return True

    monkeypatch.setattr(supervisor, "leader_health_alive", health_alive)

    connects = {"n": 0}
    remote_read_sends: list[anyio.abc.ObjectSendStream[Any]] = []

    @asynccontextmanager
    async def client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        connects["n"] += 1
        remote_read_send, remote_read_recv = anyio.create_memory_object_stream[Any](10)
        remote_write_send, _remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        remote_read_sends.append(remote_read_send)
        try:
            yield (remote_read_recv, remote_write_send)
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(supervisor, "streamable_http_client", client)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    async def run_proxy() -> None:
        await supervisor.run_supervised_proxy(
            leader_mcp_url="http://leader.invalid/mcp/",
            health_url="http://leader.invalid/api/health",
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_proxy)
        while not remote_read_sends:
            await anyio.sleep(0)
        # Induce a transient stream error; the leader stays healthy.
        await remote_read_sends[0].send(RuntimeError("transient sse hiccup"))
        with anyio.fail_after(2.0):
            while connects["n"] < 2:
                await anyio.sleep(0.01)
        assert connects["n"] >= 2  # reconnected in place, did not exit
        tg.cancel_scope.cancel()

    await local_in_send.aclose()


@pytest.mark.anyio
async def test_exits_after_recovery_window_with_permanently_dead_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    """A leader gone PAST the window: retries a few times, then exits (so serve.py
    respawns) — distinct from the legacy immediate exit."""

    connect_attempts = {"n": 0}

    def failing_client(_url: str, **_kwargs: Any) -> FailingClient:
        connect_attempts["n"] += 1
        return FailingClient()

    monkeypatch.setattr(supervisor, "streamable_http_client", failing_client)
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor, "reconnect_delay", lambda _attempt, *, max_delay: 0.02)
    monkeypatch.setattr(supervisor, "BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", 0.1)
    from tests._metric_recorders import RecordingCounter

    recovery = RecordingCounter()
    monkeypatch.setattr(supervisor, "_LEADER_RECOVERY", recovery)

    async def health_is_dead(_url: str) -> bool:
        return False

    monkeypatch.setattr(supervisor, "leader_health_alive", health_is_dead)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    with anyio.fail_after(3.0), pytest.raises(RuntimeError, match="leader health check failed"):
        await supervisor.run_supervised_proxy(
            leader_mcp_url="http://leader.invalid/mcp/",
            health_url="http://leader.invalid/api/health",
        )
    assert connect_attempts["n"] >= 2  # retried through the window before giving up
    assert recovery.attrs_for("outcome") == ["exhausted"]  # giving up was metered

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
            yield (remote_read_recv, remote_write_send)
        finally:
            await remote_read_send.aclose()

    monkeypatch.setattr(supervisor, "streamable_http_client", controllable_client)

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


@pytest.mark.anyio
async def test_flapping_session_is_throttled_not_hot_looped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A leader that accepts then instantly ends each session must NOT be
    reconnected in a zero-delay hot loop (the create/terminate storm). Each flap
    should apply an increasing success-path backoff via reconnect_delay — proving
    the loop throttles instead of busy-looping the leader."""
    delays: list[int] = []

    def spy_delay(attempt: int, *, max_delay: float) -> float:
        delays.append(attempt)
        return 0.001

    monkeypatch.setattr(supervisor, "reconnect_delay", spy_delay)
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor, "resolve_leader_token", lambda: "")
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)

    async def _noop_consumer(*_args: Any, **_kwargs: Any) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr(supervisor, "consume_leader_notifications", _noop_consumer)

    opens = {"n": 0}

    @asynccontextmanager
    async def flapping_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        opens["n"] += 1
        # An already-closed read stream → the remote reader's `async for` ends
        # immediately → the session ends the instant it opened (a flap).
        read_send, read_recv = anyio.create_memory_object_stream[SessionMessage](1)
        await read_send.aclose()
        write_send, _write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        yield (read_recv, write_send)

    monkeypatch.setattr(supervisor, "streamable_http_client", flapping_client)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    async def run_proxy() -> None:
        # health_url=None → _leader_recoverable() always True → the loop stays up
        # and keeps flapping until we cancel it.
        await supervisor.run_supervised_proxy(leader_mcp_url="http://leader.invalid/mcp/")

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_proxy)
        with anyio.fail_after(3.0):
            while len(delays) < 3:
                await anyio.sleep(0.005)
        tg.cancel_scope.cancel()

    await local_in_send.aclose()

    # Throttle engaged with an increasing flap counter (NOT a zero-delay hot loop,
    # which would leave `delays` empty), and it did re-open a session each round.
    assert delays[:3] == [1, 2, 3]
    assert opens["n"] >= 3


@pytest.mark.anyio
async def test_connect_then_abort_storm_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A leader that ACCEPTS the connection then aborts the session mid-stream (a
    ClientDisconnect / connection reset) must not be reconnected in a near-zero
    hot loop. This is the ERROR-path analogue of the clean-session flap: the
    `attempt` backoff counter resets on each successful connect, so without a
    duration-aware guard the follower storms at ~4/sec/follower."""
    delays: list[int] = []

    def spy_delay(attempt: int, *, max_delay: float) -> float:
        delays.append(attempt)
        return 0.001

    monkeypatch.setattr(supervisor, "reconnect_delay", spy_delay)
    monkeypatch.setattr(supervisor, "resolve_leader_url", lambda url: url)
    monkeypatch.setattr(supervisor, "resolve_leader_token", lambda: "")
    monkeypatch.setattr(supervisor.bridge_state, "record_snapshot", lambda **_kwargs: None)

    async def _noop_consumer(*_args: Any, **_kwargs: Any) -> None:
        await anyio.sleep_forever()

    monkeypatch.setattr(supervisor, "consume_leader_notifications", _noop_consumer)

    @asynccontextmanager
    async def aborting_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        # Connect SUCCEEDS (session opens), then the read stream immediately yields
        # an Exception → the remote reader re-raises it → the error path fires,
        # exactly like a ClientDisconnect abort right after the session opened.
        read_send, read_recv = anyio.create_memory_object_stream[Any](1)
        await read_send.send(ConnectionError("session aborted"))
        write_send, _write_recv = anyio.create_memory_object_stream[SessionMessage](10)
        yield (read_recv, write_send)

    monkeypatch.setattr(supervisor, "streamable_http_client", aborting_client)

    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(supervisor, "stdio_server", fake_stdio)

    async def run_proxy() -> None:
        await supervisor.run_supervised_proxy(leader_mcp_url="http://leader.invalid/mcp/")

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_proxy)
        with anyio.fail_after(3.0):
            while len(delays) < 3:
                await anyio.sleep(0.005)
        tg.cancel_scope.cancel()

    await local_in_send.aclose()

    # An open-then-abort cycle must back off with an INCREASING flap counter, not
    # a reset-to-0 hot loop. Before the error-path flap guard this was [0, 0, 0].
    assert delays[:3] == [1, 2, 3], f"connect-then-abort hot-loops instead of backing off: {delays[:6]}"
