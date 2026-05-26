# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP push-notification emitter for browser-pool session lifecycle events.

When a browser session leaves the pool (user close, agent close, shutdown), the
emitter sends a JSON-RPC notification to the connected MCP client without
waiting for the client to poll.

Notification method:  ``notifications/octowright/session_closed``

Params shape::

    {
      "instance_id": "abc123",
      "kind": "chromium",
      "label": "user-label-or-null",
      "profile": "persona-name-or-null",
      "reason": "agent_close" | "user_close" | "external_disconnect" | "shutdown",
      "log_path": "/path/to/session.jsonl"
    }

The emitter runs as a background asyncio task alongside the MCP stdio server.
It subscribes to the process-local ``session_event_bus``, converts each
``SessionClosedEvent`` into a raw ``JSONRPCNotification``, and writes it to the
active ``ServerSession``'s write stream.

**Session tracking**: the MCP server exposes its active session only via a
request-context contextvar (``request_ctx``).  We track the session explicitly
by hooking into ``run_stdio_async`` via ``run_with_notifications``, which wraps
the server run and exposes the write stream via ``_active_session_write``.

**Bridge propagation**: the follower's ``BridgeSupervisor._remote_reader`` loop
calls ``forward_remote_message`` for every message from the leader.  Since that
method calls ``local_write.send(message)`` unconditionally for any message that
is not a matched request response, server-side ``JSONRPCNotification`` frames
flow through to the follower client transparently — no bridge changes needed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification
from provide.telemetry import get_logger

from octowright.browser_pool.session_event_bus import SessionClosedEvent, session_event_bus

log = get_logger(__name__)

# The write half of the active stdio session.  Set by ``run_with_notifications``
# when the session starts and cleared on exit so stale writes don't reach a
# closed stream.
_active_session_write: Any | None = None


def _build_notification(event: SessionClosedEvent) -> SessionMessage:
    """Convert a pool event into a raw JSON-RPC notification frame."""
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/octowright/session_closed",
        params={
            "instance_id": event.instance_id,
            "kind": event.kind,
            "label": event.label,
            "profile": event.profile,
            "reason": event.reason,
            "log_path": event.log_path,
        },
    )
    return SessionMessage(JSONRPCMessage(root=notification))


async def _emit_loop() -> None:
    """Subscribe to session events and forward each one to the MCP client.

    Runs as a background asyncio task so it never blocks pool operations.
    The loop exits cleanly when cancelled (daemon shutdown, stdio EOF).
    """
    async with session_event_bus.subscribe() as sub:
        while True:
            event = await sub.get()
            write = _active_session_write
            if write is None:
                # No active session yet (daemon started but client hasn't
                # connected) or session ended — drop the notification.  The
                # client will reconcile state via ``browser_list`` on its next
                # connect.
                log.debug(
                    "octowright.mcp_notifications.no_session",
                    instance_id=event.instance_id,
                    reason=event.reason,
                )
                continue
            try:
                await write.send(_build_notification(event))
                log.debug(
                    "octowright.mcp_notifications.sent",
                    instance_id=event.instance_id,
                    reason=event.reason,
                )
            except Exception as exc:
                # Write failure means the transport closed; the MCP server will
                # detect this on its next send and begin teardown.  Log at
                # debug so a closed client doesn't spam the daemon log.
                log.debug(
                    "octowright.mcp_notifications.send_failed",
                    instance_id=event.instance_id,
                    reason=event.reason,
                    error=repr(exc),
                )


async def run_with_notifications(run_coro: Any, write_stream: Any) -> None:
    """Run the MCP server coroutine alongside the notification emitter.

    ``write_stream`` is the ``MemoryObjectSendStream[SessionMessage]`` created
    by ``stdio_server()``.  We store a reference so ``_emit_loop`` can push
    notifications without a request-context contextvar.

    The emit loop is started first as an asyncio task so it is scheduled and
    its subscription is active before the server coroutine begins.  Any
    session-closed events published before the first asyncio yield inside
    ``run_coro`` are therefore captured.

    Called by ``_run_mcp_with_notifications`` in ``serve.py``.  Not called for
    the HTTP-MCP transport (the StreamableHTTP transport manages multiple
    concurrent sessions and has its own fan-out; per-session notification
    delivery for that transport is future work).
    """
    global _active_session_write
    _active_session_write = write_stream
    emit_task = asyncio.create_task(_emit_loop(), name="octowright.mcp_notifications")
    # Yield once so the emit task runs its first ``async with subscribe()``
    # step before we enter run_coro. This ensures the subscription is active
    # for any event published during the very first await in run_coro.
    await asyncio.sleep(0)
    try:
        await run_coro
    finally:
        _active_session_write = None
        emit_task.cancel()
        try:
            await emit_task
        except (asyncio.CancelledError, Exception):
            pass


async def run_stdio_with_notifications(mcp: Any) -> None:
    """MCP stdio server + session-close notification emitter running together.

    Replicates ``FastMCP.run_stdio_async`` so we can capture the write stream
    and pass it to the notifier, bypassing the request-context contextvar that
    ``ServerSession.send_notification`` requires inside a request handler.
    """
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        server_run = mcp._mcp_server.run(read_stream, write_stream, mcp._mcp_server.create_initialization_options())
        await run_with_notifications(server_run, write_stream)


def get_emit_task_or_none() -> asyncio.Task[None] | None:
    """Return the active emit task, or None if not running.  Exposed for tests."""
    for task in asyncio.all_tasks():
        if task.get_name() == "octowright.mcp_notifications":
            return task  # type: ignore[return-value]
    return None


__all__ = [
    "get_emit_task_or_none",
    "run_stdio_with_notifications",
    "run_with_notifications",
]
