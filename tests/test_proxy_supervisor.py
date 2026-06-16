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
from mcp.types import JSONRPCError

from octowright import defaults
from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import (
    _notification,
    _progress,
    _request,
    _response,
    _tools_call,
    _tools_call_with_token,
)


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
