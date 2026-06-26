# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP push-notification emitter for browser-pool session lifecycle events.

When a browser session leaves the pool (user close, agent close, shutdown), the
emitter sends a JSON-RPC notification to the connected MCP client without
waiting for the client to poll.

Notification methods:
  * ``notifications/octowright/session_closed`` — a session left the pool.
  * ``notifications/octowright/browser_crashed`` — a crash was observed
    (``page.on("crash")``); the session may still be alive. Params carry
    ``scope`` ("renderer"/"process"), ``recovering`` (whether auto-recovery was
    scheduled), and an actionable ``hint`` instead of a close ``reason``.
  * ``notifications/octowright/browser_recovered`` — a renderer-crash recovery
    resolved; ``outcome`` is recovered|failed|exhausted (the accurate follow-up
    to a ``browser_crashed`` with ``recovering=true``).
  * ``notifications/octowright/driver_died`` — the shared driver died and these
    sessions were lost (``lost_instance_ids``); ``relaunch_mode`` says whether
    they're being auto-reopened.

session_closed params shape::

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
from typing import Any, cast

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification
from provide.telemetry import get_logger

from octowright.browser_pool.session_event_bus import (
    DriverDiedEvent,
    SessionCrashedEvent,
    SessionEvent,
    SessionRecoveredEvent,
    session_event_bus,
)

# Per-outcome guidance for a browser_recovered notification, so the LLM knows
# whether the crash self-healed (keep going) or it must relaunch.
_RECOVERY_HINTS = {
    "recovered": "the crashed page was auto-recovered (a fresh page in the same browser) — it's usable again, no relaunch needed",
    "failed": "auto-recovery failed (the browser process likely died) — relaunch it with browser_launch",
    "exhausted": "the page keeps crashing past the recovery cap — relaunch with browser_launch; the page/site may be unstable",
}

log = get_logger(__name__)

# The write half of the active stdio session.  Set by ``run_with_notifications``
# when the session starts and cleared on exit so stale writes don't reach a
# closed stream.
_active_session_write: Any | None = None


def _build_notification(event: SessionEvent) -> SessionMessage:
    """Convert a pool event into a raw JSON-RPC notification frame.

    Two shapes: a ``SessionClosedEvent`` (the session left the pool) becomes
    ``session_closed``; a ``SessionCrashedEvent`` (a crash was observed, session
    may still be alive) becomes ``browser_crashed`` with an actionable hint.
    """
    if isinstance(event, DriverDiedEvent):
        if event.relaunch_mode == "off":
            driver_hint = (
                f"the shared browser driver died — {event.lost_count} session(s) were lost. Auto-reopen is "
                "off; relaunch the browsers you need with browser_launch (see octowright_status().pool.lost_sessions)"
            )
        else:
            driver_hint = (
                f"the shared browser driver died — {event.lost_count} session(s) were lost and Octowright is "
                f"auto-reopening them ({event.relaunch_mode}); see octowright_status().pool.lost_sessions for the "
                "old→new instance_id mapping"
            )
        died = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/octowright/driver_died",
            params={
                "restart_count": event.restart_count,
                "relaunch_mode": event.relaunch_mode,
                "lost_count": event.lost_count,
                "lost_instance_ids": list(event.lost_instance_ids),
                "hint": driver_hint,
            },
        )
        return SessionMessage(JSONRPCMessage(root=died))
    if isinstance(event, SessionRecoveredEvent):
        recovered = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/octowright/browser_recovered",
            params={
                "instance_id": event.instance_id,
                "kind": event.kind,
                "label": event.label,
                "profile": event.profile,
                "outcome": event.outcome,
                "attempts": event.attempts,
                "log_path": event.log_path,
                "hint": _RECOVERY_HINTS.get(event.outcome, "renderer-crash recovery resolved"),
            },
        )
        return SessionMessage(JSONRPCMessage(root=recovered))
    if isinstance(event, SessionCrashedEvent):
        # Accurate to the auto-recovery behavior: when recovery is scheduled the
        # client should WAIT for the browser_recovered outcome, not relaunch a
        # browser that's already healing itself.
        hint = (
            "the page crashed — Octowright is auto-recovering it (replacing the page); "
            "no action needed, wait for a browser_recovered notification (outcome=recovered "
            "means usable again; failed/exhausted means relaunch)"
            if event.recovering
            else "the page crashed and auto-recovery is off/exhausted — relaunch the browser with browser_launch"
        )
        crash = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/octowright/browser_crashed",
            params={
                "instance_id": event.instance_id,
                "kind": event.kind,
                "label": event.label,
                "profile": event.profile,
                "scope": event.scope,
                "recovering": event.recovering,
                "log_path": event.log_path,
                "hint": hint,
            },
        )
        return SessionMessage(JSONRPCMessage(root=crash))
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


def _event_id(event: SessionEvent) -> str:
    """An identifier for debug logs across all event types (DriverDiedEvent has no
    single instance_id — it carries the set of lost ids)."""
    if isinstance(event, DriverDiedEvent):
        return f"driver(lost={event.lost_count})"
    return event.instance_id


def _event_detail(event: SessionEvent) -> str:
    """Short type-agnostic descriptor for debug logs (closed reason / crash scope /
    recovery outcome / driver death)."""
    if isinstance(event, SessionCrashedEvent):
        return f"crashed:{event.scope}"
    if isinstance(event, SessionRecoveredEvent):
        return f"recovered:{event.outcome}"
    if isinstance(event, DriverDiedEvent):
        return f"driver_died:restart={event.restart_count}"
    return event.reason


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
                    instance_id=_event_id(event),
                    detail=_event_detail(event),
                )
                continue
            try:
                await write.send(_build_notification(event))
                log.debug(
                    "octowright.mcp_notifications.sent",
                    instance_id=_event_id(event),
                    detail=_event_detail(event),
                )
            except Exception as exc:
                # Write failure means the transport closed; the MCP server will
                # detect this on its next send and begin teardown.  Log at
                # debug so a closed client doesn't spam the daemon log.
                log.debug(
                    "octowright.mcp_notifications.send_failed",
                    instance_id=_event_id(event),
                    detail=_event_detail(event),
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
            return cast("asyncio.Task[None]", task)
    return None


__all__ = [
    "get_emit_task_or_none",
    "run_stdio_with_notifications",
    "run_with_notifications",
]
