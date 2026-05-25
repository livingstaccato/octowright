# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Concurrency / reconnect stress tests for the follower bridge supervisor.

These tests complement :mod:`tests.test_proxy_supervisor` (happy-path
coverage) by hammering ``BridgeSupervisor`` with overlapping requests,
mid-flight writer drops, and burst timeouts. The architectural review
flagged this surface as the biggest test gap — the supervisor's
correctness depends entirely on in-flight bookkeeping holding under
out-of-order responses and racy reconnects.
"""

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


class _CapturingRemoteWrite:
    """Records every outbound send for later inspection."""

    def __init__(self) -> None:
        self.sent: list[SessionMessage] = []

    async def send(self, message: SessionMessage) -> None:
        self.sent.append(message)


class _DroppingRemoteWrite:
    """A remote writer whose ``send`` returns success but records nothing.

    Used to model "send went through but the response will never arrive"
    so deadline-watch logic can be exercised separately from the writer
    failure path.
    """

    async def send(self, _message: SessionMessage) -> None:
        return None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _is_bridge_error(message: SessionMessage, *, contains: str | None = None) -> bool:
    root = message.message.root
    if not isinstance(root, JSONRPCError):
        return False
    return not (contains is not None and contains not in root.error.message)


def _bridge_error_id(message: SessionMessage) -> str | int | None:
    root = message.message.root
    if isinstance(root, JSONRPCError):
        return root.id
    return None


# ---------------------------------------------------------------------------
# 1. Out-of-order response delivery under concurrent fan-in
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_burst_of_concurrent_requests_all_get_responses() -> None:
    """10 overlapping requests with responses delivered in REVERSE order.

    Verifies the in-flight dict matches request_id correctly regardless of
    arrival order, and that no bridge errors leak onto local_write.
    """
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](64)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](64)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=5.0,
    )
    remote = _CapturingRemoteWrite()
    slot = supervisor._RemoteWriteSlot(write=remote)

    async with anyio.create_task_group() as tg:
        for i in range(10):
            tg.start_soon(supervisor_obj.forward_one_local_message, _request("tools/call", f"req-{i}"), slot)

    assert supervisor_obj.in_flight_count == 10
    assert len(remote.sent) == 10

    # Deliver responses in REVERSE order to force the dict to look up by id.
    for i in reversed(range(10)):
        await supervisor_obj.forward_remote_message(_response(f"req-{i}"))

    received_ids: list[str | int | None] = []
    for _ in range(10):
        msg = await outgoing_recv.receive()
        assert not _is_bridge_error(msg), f"unexpected bridge error: {msg!r}"
        received_ids.append(supervisor.message_request_id(msg))

    assert sorted(received_ids, key=str) == sorted([f"req-{i}" for i in range(10)])
    assert supervisor_obj.in_flight_count == 0


# ---------------------------------------------------------------------------
# 2. fail_all_in_flight cleans up and a fresh request after reconnect works
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_in_flight_requests_fail_cleanly_on_reconnect() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](64)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](64)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=5.0,
    )
    remote = _CapturingRemoteWrite()
    slot = supervisor._RemoteWriteSlot(write=remote)

    for i in range(5):
        await supervisor_obj.forward_one_local_message(_request("tools/call", f"pre-{i}"), slot)
    assert supervisor_obj.in_flight_count == 5

    await supervisor_obj.fail_all_in_flight("reconnecting")

    errors_seen: set[str | int | None] = set()
    for _ in range(5):
        err = await outgoing_recv.receive()
        assert _is_bridge_error(err, contains="reconnecting")
        errors_seen.add(_bridge_error_id(err))
    assert errors_seen == {f"pre-{i}" for i in range(5)}
    assert supervisor_obj.in_flight_count == 0

    # Post-reconnect: a brand-new request still tracks fine.
    new_remote = _CapturingRemoteWrite()
    slot.write = new_remote
    await supervisor_obj.forward_one_local_message(_request("tools/call", "post-reconnect"), slot)
    assert supervisor_obj.in_flight_count == 1
    assert len(new_remote.sent) == 1
    await supervisor_obj.forward_remote_message(_response("post-reconnect"))
    response = await outgoing_recv.receive()
    assert supervisor.message_request_id(response) == "post-reconnect"
    assert supervisor_obj.in_flight_count == 0


# ---------------------------------------------------------------------------
# 3. replay_initialize is idempotent: same cached message across reconnects
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconnect_replays_initialize_only_once_per_session() -> None:
    """``_initialize_message`` is captured at track time and reused across
    reconnects without re-tracking the request_id."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](64)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](64)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=5.0,
    )

    # Two FakeRemoteConnector-style sessions, recorded so we can inspect
    # what each one received.
    connector = _SessionRecorder()

    # Initial connection: forward `initialize` once, drain it, and ack.
    session_one = await connector.connect()
    init_slot = supervisor._RemoteWriteSlot(write=session_one.remote_write)
    await supervisor_obj.forward_one_local_message(_request("initialize", "init-only"), init_slot)
    first = await session_one.received.receive()
    assert supervisor.message_method(first) == "initialize"
    assert supervisor.message_request_id(first) == "init-only"

    # Settle the in-flight entry so a later replay does NOT re-track it.
    await supervisor_obj.forward_remote_message(_response("init-only"))
    assert supervisor_obj.in_flight_count == 0

    # Reconnect #1 -> replay should resend the cached initialize with the
    # SAME request id, without inflating in_flight_count.
    session_two = await connector.connect()
    await supervisor_obj.replay_initialize(session_two.remote_write)
    replayed_one = await session_two.received.receive()
    assert supervisor.message_method(replayed_one) == "initialize"
    assert supervisor.message_request_id(replayed_one) == "init-only"
    assert supervisor_obj.in_flight_count == 0  # replay must not re-track

    # Reconnect #2 -> still the same cached message.
    session_three = await connector.connect()
    await supervisor_obj.replay_initialize(session_three.remote_write)
    replayed_two = await session_three.received.receive()
    assert supervisor.message_method(replayed_two) == "initialize"
    assert supervisor.message_request_id(replayed_two) == "init-only"
    assert supervisor_obj.in_flight_count == 0

    assert connector.connect_count == 3


class _Session:
    __slots__ = ("received", "remote_write")

    def __init__(self, received: Any, remote_write: Any) -> None:
        self.received = received
        self.remote_write = remote_write


class _SessionRecorder:
    """Same shape as ``FakeRemoteConnector`` but exposes the per-session
    receiver as ``Session.received`` for cleaner test reads."""

    def __init__(self) -> None:
        self.connect_count = 0

    async def connect(self) -> _Session:
        self.connect_count += 1
        send, recv = anyio.create_memory_object_stream[SessionMessage](16)
        return _Session(received=recv, remote_write=send)


# ---------------------------------------------------------------------------
# 4. write=None path returns a bridge error and never tracks the request
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_request_after_writer_drop_returns_bridge_error_synchronously() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](16)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](16)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=5.0,
    )
    slot = supervisor._RemoteWriteSlot(write=None)

    await supervisor_obj.forward_one_local_message(_request("tools/call", "no-writer"), slot)

    err = await outgoing_recv.receive()
    assert _is_bridge_error(err, contains="leader session unavailable")
    assert _bridge_error_id(err) == "no-writer"
    assert supervisor_obj.in_flight_count == 0  # never tracked


# ---------------------------------------------------------------------------
# 5. Race: writer drops mid-burst — every request either sends or bridge-errors
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_concurrent_request_and_reconnect_race() -> None:
    """3 concurrent forwards while the slot's writer flips to None mid-burst.

    Post-condition: every request is accounted for as either tracked (sent
    successfully before the drop) or bridge-errored (hit None). No
    AssertionError, no lost messages.
    """
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](16)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](16)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=5.0,
    )
    remote = _CapturingRemoteWrite()
    slot = supervisor._RemoteWriteSlot(write=remote)

    async def _drop_writer() -> None:
        # Yield once so at least one forward gets a chance to send first;
        # this models a leader disconnect arriving mid-burst.
        await anyio.sleep(0)
        slot.write = None

    async with anyio.create_task_group() as tg:
        for i in range(3):
            tg.start_soon(supervisor_obj.forward_one_local_message, _request("tools/call", f"race-{i}"), slot)
        tg.start_soon(_drop_writer)

    # Count bridge errors that landed on the outgoing stream.
    bridge_errors_emitted = 0
    while True:
        with anyio.move_on_after(0.05):
            msg = await outgoing_recv.receive()
            if _is_bridge_error(msg, contains="leader session unavailable"):
                bridge_errors_emitted += 1
            continue
        break

    tracked = supervisor_obj.in_flight_count
    sent_successfully = len(remote.sent)

    # Every request must be accounted for exactly once.
    assert tracked + bridge_errors_emitted == 3
    # Tracked == sent (no tracking without an outbound send).
    assert tracked == sent_successfully


# ---------------------------------------------------------------------------
# 6. watch_deadlines fires for every entry in a burst-tracked batch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_request_timeout_fires_even_under_load() -> None:
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](64)
    outgoing_send, outgoing_recv = anyio.create_memory_object_stream[SessionMessage](64)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=0.1,
    )
    remote = _DroppingRemoteWrite()
    slot = supervisor._RemoteWriteSlot(write=remote)

    for i in range(20):
        await supervisor_obj.forward_one_local_message(_request("tools/call", f"slow-{i}"), slot)
    assert supervisor_obj.in_flight_count == 20

    async with anyio.create_task_group() as tg:
        tg.start_soon(supervisor_obj.watch_deadlines)
        timed_out_ids: set[str | int | None] = set()
        for _ in range(20):
            err = await outgoing_recv.receive()
            assert _is_bridge_error(err, contains="timed out")
            timed_out_ids.add(_bridge_error_id(err))
        tg.cancel_scope.cancel()

    assert timed_out_ids == {f"slow-{i}" for i in range(20)}
    assert supervisor_obj.in_flight_count == 0
    assert supervisor_obj.request_timeouts == 20


class _RaceyRemoteWrite:
    """Send fails AFTER the slot has been swapped to a fresh writer.

    Models the exact race Gemini flagged on PR #50: while the outbound
    ``send`` is awaiting, ``_remote_supervisor`` reconnects and installs
    a new writer in the slot. The old writer's failure must not clear
    the new writer.
    """

    def __init__(self, slot: supervisor._RemoteWriteSlot, swap_in: Any) -> None:
        self._slot = slot
        self._swap_in = swap_in

    async def send(self, _message: SessionMessage) -> None:
        # Simulate the reconnect happening during the await: another
        # coroutine has reset the slot to a brand-new writer before our
        # exception surfaces.
        self._slot.write = self._swap_in
        raise ConnectionResetError("old remote stream closed")


@pytest.mark.anyio
async def test_failed_send_does_not_clear_freshly_reconnected_writer() -> None:
    """Race regression: clearing the slot on send failure must check
    that the slot still holds the writer we tried to send through."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )
    fresh_writer = _CapturingRemoteWrite()
    slot = supervisor._RemoteWriteSlot()
    slot.write = _RaceyRemoteWrite(slot, swap_in=fresh_writer)

    await supervisor_obj.forward_one_local_message(_request("tools/call", "raced"), slot)

    # Failure of the old writer must NOT have nuked the freshly installed one.
    assert slot.write is fresh_writer


@pytest.mark.anyio
async def test_failed_send_clears_slot_when_no_reconnect_raced() -> None:
    """Companion to the race-regression test: the normal failure path
    (no reconnect happened during the await) still clears the slot."""
    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)
    supervisor_obj = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=1.0,
    )

    class _PlainFailingWrite:
        async def send(self, _message: SessionMessage) -> None:
            raise ConnectionResetError("remote stream closed")

    failing = _PlainFailingWrite()
    slot = supervisor._RemoteWriteSlot(write=failing)

    await supervisor_obj.forward_one_local_message(_request("tools/call", "plain-fail"), slot)

    assert slot.write is None
