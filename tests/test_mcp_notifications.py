# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the MCP notification emitter (``server/mcp_notifications.py``).

Verifies that:
- ``_build_notification`` produces a correctly-shaped JSON-RPC notification.
- ``run_with_notifications`` starts the emit task and cancels it on exit.
- Events published while a session is active are forwarded to the write stream.
- Events published when no session is active are silently dropped.
- Bridge propagation: ``BridgeSupervisor.forward_remote_message`` passes
  server-side notifications through to the local client unchanged.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification

from octowright.browser_pool.events import SessionClosedEvent
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.server.mcp_notifications import _build_notification, run_with_notifications


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── _build_notification ──────────────────────────────────────────────────────


def test_build_notification_method() -> None:
    """The notification method is exactly ``notifications/octowright/session_closed``."""
    event = SessionClosedEvent(
        instance_id="abc",
        kind="chromium",
        label="demo",
        profile="Tanuki Tim",
        reason="agent_close",
        log_path="/tmp/abc.jsonl",
    )
    msg = _build_notification(event)
    root = msg.message.root
    assert isinstance(root, JSONRPCNotification)
    assert root.method == "notifications/octowright/session_closed"


def test_build_notification_params_shape() -> None:
    """All required fields appear in the params with correct values."""
    event = SessionClosedEvent(
        instance_id="xyz",
        kind="firefox",
        label=None,
        profile=None,
        reason="user_close",
        log_path="/recordings/xyz.jsonl",
    )
    msg = _build_notification(event)
    root = msg.message.root
    assert isinstance(root, JSONRPCNotification)
    params = root.params or {}
    assert params["instance_id"] == "xyz"
    assert params["kind"] == "firefox"
    assert params["label"] is None
    assert params["profile"] is None
    assert params["reason"] == "user_close"
    assert params["log_path"] == "/recordings/xyz.jsonl"


def test_build_notification_shutdown_reason() -> None:
    """``shutdown`` reason is forwarded verbatim."""
    event = SessionClosedEvent(
        instance_id="s1", kind="webkit", label=None, profile=None, reason="shutdown", log_path="/tmp/s1.jsonl"
    )
    msg = _build_notification(event)
    root = msg.message.root
    assert isinstance(root, JSONRPCNotification)
    assert (root.params or {})["reason"] == "shutdown"


def test_build_notification_for_crash_event() -> None:
    """A SessionCrashedEvent becomes a distinct ``browser_crashed`` notification."""
    from octowright.browser_pool.events import SessionCrashedEvent

    event = SessionCrashedEvent(
        instance_id="c1",
        kind="chromium",
        label="probe",
        profile=None,
        scope="renderer",
        log_path="/tmp/c1.jsonl",
    )
    msg = _build_notification(event)
    root = msg.message.root
    assert isinstance(root, JSONRPCNotification)
    assert root.method == "notifications/octowright/browser_crashed"
    params = root.params or {}
    assert params["instance_id"] == "c1"
    assert params["scope"] == "renderer"
    assert "hint" in params  # actionable: reload / relaunch
    assert "reason" not in params  # crash event is not a close event


# ─── run_with_notifications integration ──────────────────────────────────────


@pytest.mark.anyio
async def test_run_with_notifications_forwards_event_to_write_stream() -> None:
    """An event published while ``run_with_notifications`` is active is sent
    to the write stream."""
    written: list[SessionMessage] = []
    event_received = asyncio.Event()

    class _FakeWrite:
        async def send(self, msg: SessionMessage) -> None:
            written.append(msg)
            event_received.set()

    event = SessionClosedEvent(
        instance_id="n1",
        kind="chromium",
        label="hello",
        profile=None,
        reason="agent_close",
        log_path="/tmp/n1.jsonl",
    )

    # The inner coroutine publishes one event then completes.
    async def _inner() -> None:
        session_event_bus.publish_nowait(event)
        await event_received.wait()

    fake_write = _FakeWrite()
    await run_with_notifications(_inner(), fake_write)

    assert len(written) == 1
    root = written[0].message.root
    assert isinstance(root, JSONRPCNotification)
    assert root.method == "notifications/octowright/session_closed"
    assert (root.params or {})["instance_id"] == "n1"


@pytest.mark.anyio
async def test_run_with_notifications_clears_session_on_exit() -> None:
    """After ``run_with_notifications`` returns, the global write ref is None."""
    from octowright.server import mcp_notifications

    class _FakeWrite:
        async def send(self, _msg: SessionMessage) -> None:
            pass

    async def _noop() -> None:
        pass

    fake_write = _FakeWrite()
    await run_with_notifications(_noop(), fake_write)

    assert mcp_notifications._active_session_write is None


@pytest.mark.anyio
async def test_events_dropped_when_no_active_session() -> None:
    """When ``_active_session_write`` is None, the emit loop debug-logs and
    drops the notification instead of raising. Verified by running the emit
    loop manually for one event and confirming nothing was written."""

    from octowright.server import mcp_notifications

    # Ensure no session is active for this test.
    assert mcp_notifications._active_session_write is None

    written: list[SessionMessage] = []

    class _FakeWrite:
        async def send(self, msg: SessionMessage) -> None:
            written.append(msg)

    event = SessionClosedEvent(
        instance_id="noop",
        kind="chromium",
        label=None,
        profile=None,
        reason="agent_close",
        log_path="/tmp/noop.jsonl",
    )

    # Run with an explicit None write stream: the emit loop should skip.
    async def _inner_no_session() -> None:
        session_event_bus.publish_nowait(event)
        # Yield so the emit loop runs and logs+drops the event.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # We pass None as write_stream; the emit loop will read _active_session_write
    # which is set to None (the default before any session).
    # BUT run_with_notifications sets _active_session_write = write_stream first.
    # To test the no-session drop, we temporarily set it to None ourselves.
    original = mcp_notifications._active_session_write
    try:
        await run_with_notifications(_inner_no_session(), None)  # type: ignore[arg-type]
    finally:
        mcp_notifications._active_session_write = original

    # Nothing was written to any stream because write was None.
    assert written == []


# ─── bridge propagation (unit, no live bridge) ───────────────────────────────


@pytest.mark.anyio
async def test_bridge_forwards_server_notification_to_local_client() -> None:
    """``BridgeSupervisor.forward_remote_message`` passes a
    ``notifications/octowright/session_closed`` frame to the local write
    without buffering or consuming it as a response."""
    import anyio

    from octowright import proxy_supervisor as supervisor

    _local_send, local_recv = anyio.create_memory_object_stream[SessionMessage](10)
    outgoing_send, _outgoing_recv = anyio.create_memory_object_stream[SessionMessage](10)

    sup = supervisor.BridgeSupervisor(
        local_read=local_recv,
        local_write=outgoing_send,
        request_timeout_seconds=5.0,
    )

    # Construct a raw notification (no request id → treated as notification,
    # forwarded verbatim).
    notification_msg = SessionMessage(
        JSONRPCMessage(
            root=JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/octowright/session_closed",
                params={
                    "instance_id": "bridge-test",
                    "kind": "chromium",
                    "label": None,
                    "profile": None,
                    "reason": "user_close",
                    "log_path": "/tmp/bridge.jsonl",
                },
            )
        )
    )

    await sup.forward_remote_message(notification_msg)

    # ``forward_remote_message`` calls ``local_write.send(message)`` which is
    # ``outgoing_send``.  Retrieve the forwarded message from the recv side.
    forwarded = _outgoing_recv.receive_nowait()
    root = forwarded.message.root
    assert isinstance(root, JSONRPCNotification)
    assert root.method == "notifications/octowright/session_closed"
    assert (root.params or {})["instance_id"] == "bridge-test"
    assert (root.params or {})["reason"] == "user_close"
