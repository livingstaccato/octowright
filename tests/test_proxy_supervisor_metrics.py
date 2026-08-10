# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError

from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import (
    _notification,
    _request,
    _response,
)


class _RecordingHistogram:
    def __init__(self) -> None:
        self.records: list[tuple[float, dict[str, Any]]] = []

    def record(self, value: float, attributes: dict[str, Any] | None = None, **_kwargs: Any) -> None:
        self.records.append((value, dict(attributes or {})))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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
        JSONRPCError(
            jsonrpc="2.0",
            id="rpc-err",
            error=__import__("mcp.types", fromlist=["ErrorData"]).ErrorData(code=-32000, message="leader bailed"),
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
    assert "leader session unavailable" in err.message.error.message
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
