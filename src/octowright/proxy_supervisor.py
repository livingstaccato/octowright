# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from octowright import bridge_state, singleton
from octowright._trace_propagation import tracing_httpx_client_factory
from octowright._tracing import counter, span
from octowright.defaults import (
    BRIDGE_CONNECT_TIMEOUT_SECONDS,
    BRIDGE_RECONNECT_MAX_SECONDS,
    BRIDGE_REQUEST_TIMEOUT_SECONDS,
    BRIDGE_STATE_PATH,
)

_BRIDGE_RECONNECT = counter(
    "octowright_bridge_reconnect_total",
    description="Times the follower bridge reconnected to the leader",
)
_BRIDGE_RPC = counter(
    "octowright_bridge_rpc_total",
    description="JSON-RPC messages forwarded local→remote, labelled by method",
)

BRIDGE_ERROR_CODE = -32000
BRIDGE_ERROR_PREFIX = "Octowright bridge error:"


def message_root(message: SessionMessage) -> Any:
    return message.message.root


def message_request_id(message: SessionMessage) -> str | int | None:
    root = message_root(message)
    if isinstance(root, (JSONRPCRequest, JSONRPCResponse, JSONRPCError)):
        return root.id
    return None


def message_method(message: SessionMessage) -> str | None:
    root = message_root(message)
    if isinstance(root, (JSONRPCRequest, JSONRPCNotification)):
        return root.method
    return None


def is_request(message: SessionMessage) -> bool:
    return isinstance(message_root(message), JSONRPCRequest)


def is_response(message: SessionMessage) -> bool:
    return isinstance(message_root(message), (JSONRPCResponse, JSONRPCError))


def bridge_error(request_id: str | int, reason: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCError(
                jsonrpc="2.0",
                id=request_id,
                error=ErrorData(
                    code=BRIDGE_ERROR_CODE,
                    message=f"{BRIDGE_ERROR_PREFIX} {reason}",
                ),
            )
        )
    )


@dataclass
class InFlightRequest:
    request_id: str | int
    method: str | None
    started_at: float
    deadline: float


class BridgeSupervisor:
    def __init__(
        self,
        *,
        local_read: Any,
        local_write: Any,
        request_timeout_seconds: float,
    ) -> None:
        self.local_read = local_read
        self.local_write = local_write
        self.request_timeout_seconds = request_timeout_seconds
        self._in_flight: dict[str | int, InFlightRequest] = {}
        self._initialize_message: SessionMessage | None = None
        self.request_timeouts = 0
        self.last_error: str | None = None
        self.remote_session_id: str | None = None
        self.reconnect_attempts = 0

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    async def forward_one_local_message(self, message: SessionMessage, remote_write_box: dict[str, Any]) -> None:
        remote_write = remote_write_box.get("remote_write")
        request_id = message_request_id(message)
        method = message_method(message) or "notification"
        if remote_write is None:
            if is_request(message) and request_id is not None:
                await self.local_write.send(bridge_error(request_id, "leader session unavailable; retry"))
            return
        # One span per forwarded message. method="tools/call" carries the
        # tool name in params; we keep the span coarse here and let the
        # leader-side @mcp.tool wrapper produce the per-tool child span.
        with span("octowright.bridge.forward_rpc", method=method, request_id=request_id):
            self.track_local_message(message)
            _BRIDGE_RPC.add(1, attributes={"method": method})
            try:
                await remote_write.send(message)
            except Exception:
                remote_write_box.pop("remote_write", None)
                await self.fail_all_in_flight("leader session unavailable; retry")

    def track_local_message(self, message: SessionMessage) -> None:
        request_id = message_request_id(message)
        if is_request(message) and message_method(message) == "initialize":
            self._initialize_message = message
        if is_request(message) and request_id is not None:
            now = time.monotonic()
            self._in_flight[request_id] = InFlightRequest(
                request_id=request_id,
                method=message_method(message),
                started_at=now,
                deadline=now + self.request_timeout_seconds,
            )

    async def replay_initialize(self, remote_write: Any) -> None:
        if self._initialize_message is not None:
            await remote_write.send(self._initialize_message)

    async def forward_remote_message(self, message: SessionMessage) -> None:
        request_id = message_request_id(message)
        if request_id is not None:
            self._in_flight.pop(request_id, None)
        await self.local_write.send(message)

    async def watch_deadlines(self, interval: float = 0.01) -> None:
        while True:
            await anyio.sleep(interval)
            now = time.monotonic()
            expired = [item for item in self._in_flight.values() if item.deadline <= now]
            for item in expired:
                self._in_flight.pop(item.request_id, None)
                self.request_timeouts += 1
                self.last_error = f"request {item.request_id!r} timed out while waiting for leader response"
                await self.local_write.send(bridge_error(item.request_id, self.last_error))

    async def fail_all_in_flight(self, reason: str) -> None:
        pending = list(self._in_flight.values())
        self._in_flight.clear()
        self.last_error = reason
        for item in pending:
            await self.local_write.send(bridge_error(item.request_id, reason))


def resolve_leader_url(fallback_url: str) -> str:
    info = singleton.read_lock()
    if info is not None and not singleton.is_stale(info):
        return info.mcp_url
    return fallback_url


def reconnect_delay(attempt: int, *, max_delay: float) -> float:
    if attempt >= 4:
        return max_delay
    base = 0.25 * (2**attempt)
    return min(base, max_delay)


async def monitor_leader_health(
    cancel_scope: anyio.CancelScope,
    health_url: str,
    interval: float,
    max_failures: int,
) -> None:
    failures = 0
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            await anyio.sleep(interval)
            try:
                response = await client.get(health_url)
                ok = response.status_code == 200
            except (httpx.HTTPError, OSError):
                ok = False
            if ok:
                failures = 0
                continue
            failures += 1
            if failures >= max_failures:
                cancel_scope.cancel()
                return


async def run_supervised_proxy(
    *,
    leader_mcp_url: str,
    health_url: str | None = None,
    heartbeat_interval: float = 10.0,
    heartbeat_max_failures: int = 3,
) -> None:
    async with stdio_server() as (local_read, local_write):
        supervisor_obj = BridgeSupervisor(
            local_read=local_read,
            local_write=local_write,
            request_timeout_seconds=BRIDGE_REQUEST_TIMEOUT_SECONDS,
        )
        async with anyio.create_task_group() as local_tg:
            remote_write_box: dict[str, Any] = {}

            async def _local_forwarder() -> None:
                async for message in local_read:
                    await supervisor_obj.forward_one_local_message(message, remote_write_box)
                local_tg.cancel_scope.cancel()

            async def _remote_supervisor() -> None:
                attempt = 0
                while True:
                    remote_url = resolve_leader_url(leader_mcp_url)
                    try:
                        with anyio.fail_after(BRIDGE_CONNECT_TIMEOUT_SECONDS):
                            # Custom factory installs a request hook that
                            # injects W3C traceparent so the leader's spans
                            # chain under the follower's bridge span.
                            async with streamablehttp_client(
                                remote_url,
                                httpx_client_factory=tracing_httpx_client_factory(),
                            ) as (remote_read, remote_write, get_sid):
                                remote_write_box["remote_write"] = remote_write
                                try:
                                    supervisor_obj.remote_session_id = get_sid()
                                except Exception:
                                    supervisor_obj.remote_session_id = None
                                supervisor_obj.reconnect_attempts = attempt
                                bridge_state.record_snapshot(
                                    path=BRIDGE_STATE_PATH,
                                    follower_pid=__import__("os").getpid(),
                                    remote_url=remote_url,
                                    remote_session_id=supervisor_obj.remote_session_id,
                                    last_error=supervisor_obj.last_error,
                                    in_flight=supervisor_obj.in_flight_count,
                                    reconnect_attempts=supervisor_obj.reconnect_attempts,
                                    request_timeouts=supervisor_obj.request_timeouts,
                                )
                                await supervisor_obj.replay_initialize(remote_write)
                                attempt = 0
                                async with anyio.create_task_group() as remote_tg:

                                    async def _remote_reader() -> None:
                                        async for message in remote_read:
                                            if isinstance(message, Exception):
                                                raise message
                                            await supervisor_obj.forward_remote_message(message)

                                    remote_tg.start_soon(_remote_reader)
                                    if health_url is not None:
                                        remote_tg.start_soon(
                                            monitor_leader_health,
                                            remote_tg.cancel_scope,
                                            health_url,
                                            heartbeat_interval,
                                            heartbeat_max_failures,
                                        )
                    except Exception as exc:
                        remote_write_box.pop("remote_write", None)
                        _BRIDGE_RECONNECT.add(1, attributes={"reason": type(exc).__name__})
                        await supervisor_obj.fail_all_in_flight(f"remote leader session reset: {exc!r}")
                        supervisor_obj.last_error = repr(exc)
                        bridge_state.record_snapshot(
                            path=BRIDGE_STATE_PATH,
                            follower_pid=__import__("os").getpid(),
                            remote_url=remote_url,
                            remote_session_id=supervisor_obj.remote_session_id,
                            last_error=supervisor_obj.last_error,
                            in_flight=supervisor_obj.in_flight_count,
                            reconnect_attempts=attempt,
                            request_timeouts=supervisor_obj.request_timeouts,
                        )
                        await anyio.sleep(reconnect_delay(attempt, max_delay=BRIDGE_RECONNECT_MAX_SECONDS))
                        attempt += 1

            local_tg.start_soon(_local_forwarder)
            local_tg.start_soon(_remote_supervisor)
            local_tg.start_soon(supervisor_obj.watch_deadlines)
