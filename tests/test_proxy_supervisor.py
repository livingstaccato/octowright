# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import time
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from octowright import defaults
from octowright import proxy_runtime as runtime
from octowright import proxy_supervisor as supervisor


def _request(method: str, request_id: str = "r1") -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params={"x": 1}))
    )


def _tools_call(tool: str, request_id: str = "tc1") -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=request_id,
                method="tools/call",
                params={"name": tool, "arguments": {}},
            )
        )
    )


def _tools_call_with_token(tool: str, request_id: str, token: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=request_id,
                method="tools/call",
                params={"name": tool, "arguments": {}, "_meta": {"progressToken": token}},
            )
        )
    )


def _progress(token: str, progress: float = 1.0) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/progress",
                params={"progressToken": token, "progress": progress},
            )
        )
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
    msg = root.error.message
    # Prefix + the specific reason are preserved up front.
    assert msg.startswith("Octowright bridge error: remote request timed out")
    # Standing guidance is appended so the agent that sees this error is steered
    # away from faking a browser with a shell `open` and toward reconnecting the
    # MCP client — the exact failure this message exists to prevent.
    assert supervisor.BRIDGE_ERROR_GUIDANCE in msg
    assert "reconnect" in msg.lower()
    assert "open" in msg.lower()  # names the shell-fallback trap explicitly


def test_per_tool_timeout_floor_for_long_tools() -> None:
    """A tools/call for a long-running tool (browser_launch) gets its per-tool
    deadline from BRIDGE_TOOL_TIMEOUTS, well above the flat request timeout."""
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=None, request_timeout_seconds=20.0)
    sup.track_local_message(_tools_call("browser_launch", "bl1"))
    in_flight = sup._in_flight["bl1"]
    budget = in_flight.deadline - in_flight.started_at
    assert budget == pytest.approx(defaults.BRIDGE_TOOL_TIMEOUTS["browser_launch"], rel=0.01)
    assert budget > 20.0  # larger than the flat default the bridge was constructed with


def test_unlisted_tool_uses_flat_timeout() -> None:
    """A tools/call for a tool with no per-tool override falls back to the flat
    request timeout the supervisor was constructed with."""
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=None, request_timeout_seconds=20.0)
    sup.track_local_message(_tools_call("browser_click", "bc1"))
    in_flight = sup._in_flight["bc1"]
    assert in_flight.deadline - in_flight.started_at == pytest.approx(20.0, rel=0.01)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_request_timeout_returns_bridge_error() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=0.05,
    )

    # Production sends a single message through `forward_one_local_message`
    # which both tracks the in-flight request and forwards it to the remote.
    # Here we exercise the in-flight bookkeeping directly so the timeout
    # watchdog has something to expire.
    request = _request("tools/call", "timeout-id")
    supervisor_obj.track_local_message(request)

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.watch_deadlines)
        error = await outgoing_recv.receive()
        root = error.message.root
        assert isinstance(root, JSONRPCError)
        assert root.id == "timeout-id"
        assert "timed out" in root.error.message
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_remote_response_clears_in_flight() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    supervisor_obj.track_local_message(_request("tools/call", "ok-id"))
    assert supervisor_obj.in_flight_count == 1

    await supervisor_obj.forward_remote_message(_response("ok-id"))
    response = await outgoing_recv.receive()
    assert supervisor.message_request_id(response) == "ok-id"
    assert supervisor_obj.in_flight_count == 0


@pytest.mark.anyio
async def test_synthetic_progress_token_injected_on_tools_call() -> None:
    """Forwarding a tools/call with no client progressToken injects a synthetic
    one into _meta (so the leader emits progress the bridge can act on) and
    records it as synthetic."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    slot = supervisor._RemoteWriteSlot(write=remote_send)

    await sup.forward_one_local_message(_tools_call("browser_launch", "bl1"), slot)

    sent = await remote_recv.receive()
    params = sent.message.root.params
    token = params["_meta"]["progressToken"]
    assert token  # a synthetic token was injected
    assert token in sup._synthetic_progress_tokens
    assert sup._in_flight["bl1"].progress_token == token


@pytest.mark.anyio
async def test_progress_notification_rearms_in_flight_deadline() -> None:
    """A progress notification for an in-flight request pushes its deadline out,
    so a steadily-progressing op isn't killed by the watchdog."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, _remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    slot = supervisor._RemoteWriteSlot(write=remote_send)

    await sup.forward_one_local_message(_tools_call("browser_click", "c1"), slot)
    token = sup._in_flight["c1"].progress_token
    sup._in_flight["c1"].deadline = 0.0  # pretend it's about to expire

    await sup.forward_remote_message(_progress(token))

    assert sup._in_flight["c1"].deadline > time.monotonic()


@pytest.mark.anyio
async def test_synthetic_progress_notification_is_swallowed() -> None:
    """A progress notification for a bridge-injected token re-arms the deadline
    but is NOT forwarded to the client (the client never asked for it)."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, _remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    slot = supervisor._RemoteWriteSlot(write=remote_send)

    await sup.forward_one_local_message(_tools_call("browser_click", "c1"), slot)
    token = sup._in_flight["c1"].progress_token
    await sup.forward_remote_message(_progress(token))

    with pytest.raises(anyio.WouldBlock):
        outgoing_recv.receive_nowait()  # nothing forwarded to the client


@pytest.mark.anyio
async def test_client_progress_token_is_forwarded_and_rearms() -> None:
    """When the client supplied its own progressToken, the bridge does NOT
    rewrite it, re-arms the deadline on progress, AND forwards the notification
    through (the client asked for it)."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, _remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    slot = supervisor._RemoteWriteSlot(write=remote_send)

    await sup.forward_one_local_message(_tools_call_with_token("browser_click", "c2", "client-tok"), slot)
    assert sup._in_flight["c2"].progress_token == "client-tok"
    assert "client-tok" not in sup._synthetic_progress_tokens
    sup._in_flight["c2"].deadline = 0.0

    await sup.forward_remote_message(_progress("client-tok"))

    forwarded = outgoing_recv.receive_nowait()
    assert supervisor.message_method(forwarded) == "notifications/progress"
    assert sup._in_flight["c2"].deadline > time.monotonic()


@pytest.mark.anyio
async def test_idempotency_key_injected_on_tools_call() -> None:
    """A tools/call carries a bridge-injected idempotency key in _meta, stored on
    the InFlightRequest so a reconnect can re-send it verbatim for safe dedup."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    slot = supervisor._RemoteWriteSlot(write=remote_send)

    await sup.forward_one_local_message(_tools_call("browser_launch", "bl1"), slot)

    sent = await remote_recv.receive()
    key = sent.message.root.params["_meta"]["octowrightIdempotencyKey"]
    assert key.startswith("owk-")
    assert sup._in_flight["bl1"].idempotency_key == key


@pytest.mark.anyio
async def test_no_idempotency_key_on_non_tools_call() -> None:
    """Non-tools/call requests (initialize, tools/list) carry no idempotency key —
    only side-effect-capable tool calls need dedup."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    slot = supervisor._RemoteWriteSlot(write=remote_send)

    await sup.forward_one_local_message(_request("tools/list", "tl1"), slot)

    sent = await remote_recv.receive()
    params = sent.message.root.params or {}
    assert "octowrightIdempotencyKey" not in params.get("_meta", {})
    assert sup._in_flight["tl1"].idempotency_key is None


@pytest.mark.anyio
async def test_idempotency_keys_are_unique_per_request() -> None:
    """Each logical tools/call gets a distinct key (uuid4-based), so two genuinely
    separate calls never collide in the leader's dedup cache."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote_send, _remote_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    slot = supervisor._RemoteWriteSlot(write=remote_send)

    await sup.forward_one_local_message(_tools_call("browser_click", "a"), slot)
    await sup.forward_one_local_message(_tools_call("browser_click", "b"), slot)

    assert sup._in_flight["a"].idempotency_key != sup._in_flight["b"].idempotency_key


def _resume_supervisor() -> tuple[Any, Any, Any]:
    """A supervisor plus its client-facing recv and a fresh remote send/recv pair."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=local_recv, local_write=outgoing_send, request_timeout_seconds=20.0)
    return sup, outgoing_recv, _local_send


@pytest.mark.anyio
async def test_resume_resends_in_flight_with_same_key_on_reconnect() -> None:
    """On reconnect, a still-in-flight keyed tools/call is re-sent verbatim (same
    id, same idempotency key) on the new session — no client error in between."""
    sup, outgoing_recv, _ = _resume_supervisor()
    remote1_send, remote1_recv = anyio.create_memory_object_stream[SessionMessage](10)
    remote2_send, remote2_recv = anyio.create_memory_object_stream[SessionMessage](10)

    await sup.forward_one_local_message(_tools_call("browser_launch", "bl1"), supervisor._RemoteWriteSlot(remote1_send))
    sent1 = await remote1_recv.receive()
    key = sent1.message.root.params["_meta"]["octowrightIdempotencyKey"]

    await sup.fail_or_mark_for_resume("remote leader session reset")
    assert "bl1" in sup._in_flight  # resumable: kept, not failed
    with pytest.raises(anyio.WouldBlock):
        outgoing_recv.receive_nowait()  # client saw no bridge error

    await sup.resume_in_flight(remote2_send)
    sent2 = await remote2_recv.receive()
    assert sent2.message.root.id == "bl1"
    assert sent2.message.root.params["_meta"]["octowrightIdempotencyKey"] == key
    assert sup._in_flight["bl1"].resume_count == 1


@pytest.mark.anyio
async def test_non_resumable_request_fails_on_reset() -> None:
    """A non-tools/call (no idempotency key) is failed with the retry-hint on a
    reset — it can't be safely re-sent."""
    sup, outgoing_recv, _ = _resume_supervisor()
    remote_send, _remote_recv = anyio.create_memory_object_stream[SessionMessage](10)

    await sup.forward_one_local_message(_request("tools/list", "tl1"), supervisor._RemoteWriteSlot(remote_send))
    await sup.fail_or_mark_for_resume("remote leader session reset")

    err = outgoing_recv.receive_nowait()
    assert supervisor.message_request_id(err) == "tl1"
    assert "tl1" not in sup._in_flight


@pytest.mark.anyio
async def test_resume_rearms_deadline() -> None:
    """Resuming re-arms the deadline so the watchdog doesn't kill a freshly-resumed request."""
    sup, _outgoing_recv, _ = _resume_supervisor()
    remote_send, _remote_recv = anyio.create_memory_object_stream[SessionMessage](10)

    await sup.forward_one_local_message(_tools_call("browser_click", "c1"), supervisor._RemoteWriteSlot(remote_send))
    await sup.fail_or_mark_for_resume("reset")
    sup._in_flight["c1"].deadline = 0.0
    await sup.resume_in_flight(remote_send)
    assert sup._in_flight["c1"].deadline > time.monotonic()


@pytest.mark.anyio
async def test_resume_budget_exhausted_then_fails() -> None:
    """After BRIDGE_RESUME_MAX_ATTEMPTS resumes, the next reset fails the request."""
    sup, outgoing_recv, _ = _resume_supervisor()
    remote_send, _remote_recv = anyio.create_memory_object_stream[SessionMessage](20)

    await sup.forward_one_local_message(_tools_call("browser_click", "c1"), supervisor._RemoteWriteSlot(remote_send))
    for _ in range(defaults.BRIDGE_RESUME_MAX_ATTEMPTS):
        await sup.fail_or_mark_for_resume("reset")
        assert "c1" in sup._in_flight
        await sup.resume_in_flight(remote_send)
    assert sup._in_flight["c1"].resume_count == defaults.BRIDGE_RESUME_MAX_ATTEMPTS

    await sup.fail_or_mark_for_resume("reset")  # budget exhausted
    assert "c1" not in sup._in_flight
    err = outgoing_recv.receive_nowait()
    assert supervisor.message_request_id(err) == "c1"


@pytest.mark.anyio
async def test_kill_switch_fails_instead_of_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """With OCTOWRIGHT_IDEMPOTENCY off, a tools/call is failed on reset (today's
    fail-safe), not resumed."""
    monkeypatch.setattr(supervisor.defaults, "IDEMPOTENCY_ENABLED", False)
    sup, outgoing_recv, _ = _resume_supervisor()
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
    meta = sent.message.root.params.get("_meta", {})
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
    root = error.message.root
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

    monkeypatch.setattr(runtime.httpx, "AsyncClient", FakeClient)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            runtime.monitor_leader_health,
            tg.cancel_scope,
            "http://leader/api/health",
            0.01,
            2,
        )
        with anyio.move_on_after(1.0):
            await anyio.sleep_forever()

    assert calls >= 2


def test_backoff_sequence_caps_at_max() -> None:
    assert [runtime.reconnect_delay(i, max_delay=5.0) for i in range(6)] == [
        0.25,
        0.5,
        1.0,
        2.0,
        5.0,
        5.0,
    ]


# ---------------------------------------------------------------------------
# End-to-end RPC duration histogram + outbound forward_rpc span
# ---------------------------------------------------------------------------


class _RecordingHistogram:
    def __init__(self) -> None:
        self.records: list[tuple[float, dict[str, Any]]] = []

    def record(self, value: float, attributes: dict[str, Any] | None = None, **_kwargs: Any) -> None:
        self.records.append((value, dict(attributes or {})))


@pytest.fixture
def captured_duration(monkeypatch: pytest.MonkeyPatch) -> _RecordingHistogram:
    rec = _RecordingHistogram()
    monkeypatch.setattr(supervisor, "_BRIDGE_RPC_DURATION", rec)
    return rec


@pytest.mark.anyio
async def test_forward_remote_message_records_rpc_duration(captured_duration: _RecordingHistogram) -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    supervisor_obj.track_local_message(_request("tools/call", "rpc-1"))
    await supervisor_obj.forward_remote_message(_response("rpc-1"))
    _ = await outgoing_recv.receive()

    assert len(captured_duration.records) == 1
    value, attrs = captured_duration.records[0]
    assert value >= 0.0
    assert attrs == {"method": "tools/call", "outcome": "ok"}


@pytest.mark.anyio
async def test_forward_remote_message_records_error_outcome(captured_duration: _RecordingHistogram) -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    supervisor_obj.track_local_message(_request("tools/call", "rpc-err"))
    error_msg = SessionMessage(
        JSONRPCMessage(
            root=JSONRPCError(
                jsonrpc="2.0",
                id="rpc-err",
                error=__import__("mcp.types", fromlist=["ErrorData"]).ErrorData(code=-32000, message="leader bailed"),
            )
        )
    )
    await supervisor_obj.forward_remote_message(error_msg)
    _ = await outgoing_recv.receive()

    assert len(captured_duration.records) == 1
    _, attrs = captured_duration.records[0]
    assert attrs == {"method": "tools/call", "outcome": "error"}


@pytest.mark.anyio
async def test_forward_remote_message_without_matching_request_does_not_record(
    captured_duration: _RecordingHistogram,
) -> None:
    """Stray response with no in-flight entry must not record a histogram point."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )
    await supervisor_obj.forward_remote_message(_response("orphan-id"))
    _ = await outgoing_recv.receive()
    assert captured_duration.records == []


@pytest.mark.anyio
async def test_watch_deadlines_records_timeout_outcome(captured_duration: _RecordingHistogram) -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=0.02,
    )
    supervisor_obj.track_local_message(_request("tools/call", "timeout-rpc"))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.watch_deadlines)
        _ = await outgoing_recv.receive()
        tg.cancel_scope.cancel()

    assert len(captured_duration.records) == 1
    _, attrs = captured_duration.records[0]
    assert attrs == {"method": "tools/call", "outcome": "timeout"}


@pytest.mark.anyio
async def test_fail_all_in_flight_records_failure_outcome(captured_duration: _RecordingHistogram) -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )
    supervisor_obj.track_local_message(_request("tools/call", "fail-rpc"))
    await supervisor_obj.fail_all_in_flight("remote leader stream closed")
    _ = await outgoing_recv.receive()

    assert len(captured_duration.records) == 1
    _, attrs = captured_duration.records[0]
    assert attrs == {"method": "tools/call", "outcome": "failure"}


@pytest.mark.anyio
async def test_forward_one_local_message_opens_outbound_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """The outbound send is wrapped in an ``octowright.bridge.forward_rpc`` span."""
    seen: list[tuple[str, dict[str, Any]]] = []
    from contextlib import contextmanager

    @contextmanager
    def fake_span(name: str, **attrs: Any):  # type: ignore[no-untyped-def]
        seen.append((name, dict(attrs)))

        class _FakeSpan:
            def set_attribute(self, *_a: Any, **_kw: Any) -> None: ...

        yield _FakeSpan()

    monkeypatch.setattr(supervisor, "span", fake_span)

    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    captured_messages: list[SessionMessage] = []

    class _CapturingRemoteWrite:
        async def send(self, message: SessionMessage) -> None:
            captured_messages.append(message)

    remote_write_slot = supervisor._RemoteWriteSlot(write=_CapturingRemoteWrite())
    await supervisor_obj.forward_one_local_message(_request("tools/call", "spanned"), remote_write_slot)

    assert len(captured_messages) == 1
    assert ("octowright.bridge.forward_rpc", {"method": "tools/call", "request_id": "spanned"}) in seen


@pytest.mark.anyio
async def test_forward_one_local_message_no_remote_writer_skips_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the remote writer is None we short-circuit before opening the span."""
    seen: list[str] = []
    from contextlib import contextmanager

    @contextmanager
    def fake_span(name: str, **_attrs: Any):  # type: ignore[no-untyped-def]
        seen.append(name)
        yield None

    monkeypatch.setattr(supervisor, "span", fake_span)

    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    await supervisor_obj.forward_one_local_message(_request("tools/call", "no-leader"), supervisor._RemoteWriteSlot())
    err = await outgoing_recv.receive()
    assert "leader session unavailable" in err.message.root.error.message
    assert seen == []


@pytest.mark.anyio
async def test_forward_remote_message_notification_does_not_record(
    captured_duration: _RecordingHistogram,
) -> None:
    """A response/notification without a request_id must not poke the histogram."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )
    await supervisor_obj.forward_remote_message(_notification("notifications/cancelled"))
    _ = await outgoing_recv.receive()
    assert captured_duration.records == []


@pytest.mark.anyio
async def test_forward_one_local_message_notification_with_no_remote_drops_silently() -> None:
    """A notification when the remote is None is dropped without producing a bridge_error."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )
    await supervisor_obj.forward_one_local_message(
        _notification("notifications/initialized"), supervisor._RemoteWriteSlot()
    )
    # Nothing should land on the local outgoing stream — notifications are fire-and-forget.
    with anyio.move_on_after(0.05):
        msg = await outgoing_recv.receive()
        raise AssertionError(f"unexpected outgoing message: {msg!r}")


@pytest.mark.anyio
async def test_forward_one_local_message_notification_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Notifications still flow through the short-scope outbound span."""
    seen: list[tuple[str, dict[str, Any]]] = []
    from contextlib import contextmanager

    @contextmanager
    def fake_span(name: str, **attrs: Any):  # type: ignore[no-untyped-def]
        seen.append((name, dict(attrs)))
        yield None

    monkeypatch.setattr(supervisor, "span", fake_span)

    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    class _CapturingRemoteWrite:
        async def send(self, _message: SessionMessage) -> None: ...

    remote_write_slot = supervisor._RemoteWriteSlot(write=_CapturingRemoteWrite())
    await supervisor_obj.forward_one_local_message(_notification("notifications/initialized"), remote_write_slot)

    assert seen == [
        ("octowright.bridge.forward_rpc", {"method": "notifications/initialized", "request_id": None}),
    ]


# ---------------------------------------------------------------------------
# Bug-fix regression tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_concurrent_response_and_timeout_emits_single_frame() -> None:
    """If a response arrives in the same tick as deadline expiry, the watchdog
    must not emit a second bridge_error after the real response is delivered."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=0.05,
    )

    request_id = "race-id"
    supervisor_obj.track_local_message(_request("tools/call", request_id))

    # Drive the in-flight entry past its deadline so the watchdog would mark
    # it expired on the next wake. Then deliver the real response first; the
    # watchdog must not emit a second frame for the same request id.
    in_flight = supervisor_obj._in_flight[request_id]
    in_flight.deadline = -1.0

    await supervisor_obj.forward_remote_message(_response(request_id))

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.watch_deadlines, 0.01)
        with anyio.move_on_after(0.2):
            await anyio.sleep_forever()
        tg.cancel_scope.cancel()

    frames: list[SessionMessage] = []
    while True:
        with anyio.move_on_after(0.01):
            frames.append(await outgoing_recv.receive())
            continue
        break

    assert len(frames) == 1
    assert supervisor.message_request_id(frames[0]) == request_id
    assert isinstance(frames[0].message.root, JSONRPCResponse)


@pytest.mark.anyio
async def test_responded_flag_blocks_double_response_when_id_reused() -> None:
    """Direct check: once an in-flight entry is marked responded, neither path
    sends another frame for it. Exercises the dataclass invariant in isolation."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    supervisor_obj.track_local_message(_request("tools/call", "dup-id"))
    in_flight = supervisor_obj._in_flight["dup-id"]

    # Pretend the response already shipped.
    in_flight.responded = True

    await supervisor_obj.fail_all_in_flight("would-be second frame")

    with anyio.move_on_after(0.05):
        frame = await outgoing_recv.receive()
        raise AssertionError(f"unexpected frame after responded=True: {frame!r}")


@pytest.mark.anyio
async def test_run_supervised_proxy_session_survives_past_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connected session must not be cancelled at BRIDGE_CONNECT_TIMEOUT_SECONDS.

    The bug: ``with anyio.fail_after(...)`` previously wrapped the whole
    ``async with streamablehttp_client(...)`` block, so the body (the read
    loop, the inner task group) was also subject to the connect deadline.
    Active sessions died every BRIDGE_CONNECT_TIMEOUT_SECONDS and reconnected,
    failing every in-flight request. The fix scopes the deadline to only the
    ``__aenter__`` handshake.
    """
    from contextlib import asynccontextmanager

    # Force a tiny connect timeout so the test runs fast. The session, once
    # entered, must outlive this deadline; the read loop is unconstrained.
    monkeypatch.setattr(runtime, "BRIDGE_CONNECT_TIMEOUT_SECONDS", 0.05)

    fake_remote_read_send, fake_remote_read_recv = anyio.create_memory_object_stream[SessionMessage](10)
    fake_remote_write_send, fake_remote_write_recv = anyio.create_memory_object_stream[SessionMessage](10)

    enters: list[float] = []
    exits: list[float] = []

    @asynccontextmanager
    async def fake_client(_url: str, **_kwargs: Any):  # type: ignore[no-untyped-def]
        enters.append(__import__("time").monotonic())
        try:
            yield (fake_remote_read_recv, fake_remote_write_send, lambda: "sess-1")
        finally:
            exits.append(__import__("time").monotonic())

    monkeypatch.setattr(runtime, "streamablehttp_client", fake_client)
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda url: url)

    # No-op snapshot writer to avoid hitting disk.
    monkeypatch.setattr(runtime.bridge_state, "record_snapshot", lambda **_kwargs: None)

    # Stub stdio_server so the supervisor uses our in-memory streams.
    local_in_send, local_in_recv = anyio.create_memory_object_stream[SessionMessage](10)
    local_out_send, _local_out_recv = anyio.create_memory_object_stream[SessionMessage](10)

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        yield (local_in_recv, local_out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)

    async with anyio.create_task_group() as tg:

        async def _runner() -> None:
            await runtime.run_supervised_proxy(leader_mcp_url="http://leader.invalid/mcp")

        tg.start_soon(_runner)

        # Wait long enough that the OLD code would have cancelled the session
        # at the connect deadline (0.05s). With the fix, the session stays up.
        await anyio.sleep(0.3)

        # If the fix is in place: exactly one enter, no exit yet.
        assert len(enters) == 1, f"expected one connect, saw {len(enters)} (reconnect storm)"
        assert exits == [], f"session should still be open, but it exited: {exits}"

        tg.cancel_scope.cancel()

    # Quiet the unused stream warnings.
    await fake_remote_read_send.aclose()
    await fake_remote_write_recv.aclose()
    await local_in_send.aclose()
