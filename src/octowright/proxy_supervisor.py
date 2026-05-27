# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse
from provide.telemetry import get_logger

from octowright import bridge_state, singleton
from octowright._trace_propagation import tracing_httpx_client_factory
from octowright._tracing import counter, histogram, span
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
_BRIDGE_RPC_DURATION = histogram(
    "octowright_bridge_rpc_duration_seconds",
    description="End-to-end follower→leader→follower RPC latency, labelled by method and outcome",
    unit="s",
)

BRIDGE_ERROR_CODE = -32000
BRIDGE_ERROR_PREFIX = "Octowright bridge error:"

log = get_logger(__name__)


@dataclass
class _RemoteWriteSlot:
    """A nullable handle to the leader's incoming-stream send channel.

    The bridge has two coroutines that race for the same slot: the local
    forwarder reads it on every inbound stdio frame, and the remote
    supervisor sets/clears it on reconnect. Previously this was a
    ``dict[str, Any]`` masquerading as a nullable channel — works at runtime
    but hides the nullability from the type checker. A one-attribute
    dataclass makes the ``None`` state explicit.
    """

    write: Any | None = None


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
    # Guards against the watchdog and the remote reader both popping the
    # same id concurrently — without this, a response arriving in the same
    # asyncio tick as deadline expiry produces two outbound frames for one
    # request, which is an MCP protocol violation.
    responded: bool = False


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
        # Replays of the cached initialize use a fresh id per reconnect so the
        # leader doesn't see duplicate ids within a long-lived follower lifetime;
        # the matching response is swallowed here rather than forwarded, because
        # the local client already got its initialize response on the first try.
        self._replay_id_counter = itertools.count(1)
        self._internal_replay_ids: set[str | int] = set()
        self.request_timeouts = 0
        self.last_error: str | None = None
        self.remote_session_id: str | None = None
        self.reconnect_attempts = 0

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    async def forward_one_local_message(self, message: SessionMessage, remote_write_slot: _RemoteWriteSlot) -> None:
        remote_write = remote_write_slot.write
        request_id = message_request_id(message)
        method = message_method(message) or "notification"
        if remote_write is None:
            if is_request(message) and request_id is not None:
                await self.local_write.send(bridge_error(request_id, "leader session unavailable; retry"))
            return
        # One span per forwarded message — covers only the outbound send to
        # the leader. End-to-end follower→leader→follower latency is captured
        # separately in ``forward_remote_message`` via the
        # ``octowright_bridge_rpc_duration_seconds`` histogram, keyed off
        # ``InFlightRequest.started_at``. We deliberately don't span the
        # full RPC: spans would have to bridge two coroutines (local
        # forwarder vs. remote reader) with shared mutable state, which is
        # the kind of context-attach interleaving that's easy to get wrong
        # and hard to test deterministically. Method="tools/call" carries
        # the tool name in params; we keep the span coarse here and let
        # the leader-side @mcp.tool wrapper produce the per-tool child span.
        with span("octowright.bridge.forward_rpc", method=method, request_id=request_id):
            self.track_local_message(message)
            _BRIDGE_RPC.add(1, attributes={"method": method})
            try:
                await remote_write.send(message)
            except Exception as exc:
                # If the failed send was a notification we have nowhere to
                # return an error to — log so a missing `notifications/*`
                # round-trip (e.g. notifications/initialized after reconnect)
                # is at least visible in post-mortem.
                if not (is_request(message) and request_id is not None):
                    log.debug(
                        "octowright.bridge.notification_drop",
                        method=method,
                        error=repr(exc),
                    )
                # Only clear the slot if it still holds the writer we just
                # tried to send through. During the await above the remote
                # supervisor can reconnect and swap in a fresh writer; an
                # unconditional clear here would nuke that valid new writer
                # based on the failure of the old one.
                if remote_write_slot.write is remote_write:
                    remote_write_slot.write = None
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
        if self._initialize_message is None:
            return
        cached_root = message_root(self._initialize_message)
        if not isinstance(cached_root, JSONRPCRequest):
            return
        replay_id = f"octowright-bridge-replay-{next(self._replay_id_counter)}"
        self._internal_replay_ids.add(replay_id)
        replay_request = cached_root.model_copy(update={"id": replay_id})
        replay_message = SessionMessage(JSONRPCMessage(root=replay_request))
        await remote_write.send(replay_message)

    async def forward_remote_message(self, message: SessionMessage) -> None:
        request_id = message_request_id(message)
        if request_id is not None and request_id in self._internal_replay_ids:
            # Bridge-internal initialize replay: the local client has already
            # been told the session is initialized; forwarding a second
            # response would be a duplicate id from the client's perspective.
            self._internal_replay_ids.discard(request_id)
            return
        if request_id is not None:
            in_flight = self._in_flight.pop(request_id, None)
            if in_flight is not None:
                if in_flight.responded:
                    return
                in_flight.responded = True
                # End-to-end RPC latency: from when the follower forwarded
                # the request to when the matching response arrived from
                # the leader. Outcome label distinguishes success
                # (JSONRPCResponse) from leader-side error (JSONRPCError).
                elapsed = time.monotonic() - in_flight.started_at
                outcome = "error" if isinstance(message_root(message), JSONRPCError) else "ok"
                _BRIDGE_RPC_DURATION.record(
                    elapsed,
                    attributes={"method": in_flight.method or "unknown", "outcome": outcome},
                )
        await self.local_write.send(message)

    async def watch_deadlines(self, interval: float = 0.1) -> None:
        while True:
            await anyio.sleep(interval)
            now = time.monotonic()
            expired = [item for item in self._in_flight.values() if item.deadline <= now]
            for item in expired:
                current = self._in_flight.pop(item.request_id, None)
                if current is None or current.responded:
                    continue
                current.responded = True
                self.request_timeouts += 1
                self.last_error = f"request {current.request_id!r} timed out while waiting for leader response"
                # Record the full timeout duration so dashboards see the
                # tail latency, not just the success path.
                _BRIDGE_RPC_DURATION.record(
                    now - current.started_at,
                    attributes={"method": current.method or "unknown", "outcome": "timeout"},
                )
                await self.local_write.send(bridge_error(current.request_id, self.last_error))

    async def fail_all_in_flight(self, reason: str) -> None:
        pending = list(self._in_flight.values())
        self._in_flight.clear()
        self.last_error = reason
        now = time.monotonic()
        for item in pending:
            if item.responded:
                continue
            item.responded = True
            _BRIDGE_RPC_DURATION.record(
                now - item.started_at,
                attributes={"method": item.method or "unknown", "outcome": "failure"},
            )
            await self.local_write.send(bridge_error(item.request_id, reason))


def resolve_leader_url(fallback_url: str) -> str:
    info = singleton.read_lock()
    if info is not None and not singleton.is_stale(info):
        if _leader_url_is_safe(info.mcp_url):
            return info.mcp_url
        log.warning(
            "octowright.bridge.leader_url_rejected",
            mcp_url=info.mcp_url,
            reason="non-loopback host without OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1",
        )
    return fallback_url


def _leader_url_is_safe(mcp_url: str) -> bool:
    """Refuse to bridge to a leader URL whose host isn't loopback.

    The lockfile is writable by any process running as the same user — a
    malicious local process (poisoned pip install, sandbox escape, etc.)
    could overwrite ``mcp_url`` to redirect MCP traffic (including persona
    credentials substituted into tool args) to an attacker-controlled URL.
    Validate the host before opening the stream; allow remote URLs only
    when the operator has explicitly opted in via the env flag the HTTP
    layer already uses for the same trust boundary.
    """
    # Import lazily — http.exposure is a separate layer; we only want the
    # host classifier, not the full HTTP guard machinery.
    from urllib.parse import urlparse

    from octowright.http.exposure import is_loopback_host, remote_dashboard_allowed

    try:
        host = urlparse(mcp_url).hostname
    except ValueError:
        return False
    if host is None:
        return False
    return is_loopback_host(host) or remote_dashboard_allowed()


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
            remote_write_slot = _RemoteWriteSlot()

            async def _local_forwarder() -> None:
                async for message in local_read:
                    await supervisor_obj.forward_one_local_message(message, remote_write_slot)
                local_tg.cancel_scope.cancel()

            async def _remote_supervisor() -> None:
                attempt = 0
                # Build the tracing httpx factory once; it's a closure with no
                # per-connection state, so reusing it across reconnects avoids
                # an allocation per attempt.
                httpx_factory = tracing_httpx_client_factory()
                while True:
                    remote_url = resolve_leader_url(leader_mcp_url)
                    # Use async with (not manual __aenter__/__aexit__) so the
                    # context manager's async generator is entered and exited
                    # in the same coroutine. Python 3.13 finalizes abandoned
                    # async generators in a separate asyncio task; anyio cancel
                    # scopes cannot span task boundaries, producing
                    # "Attempted to exit cancel scope in a different task" on
                    # teardown when __aexit__ was called manually.
                    # CancelScope + deadline scopes the timeout to the connect
                    # handshake only: once inside the async with block the
                    # deadline is extended to infinity so the read loop is
                    # unconstrained and long-running sessions aren't killed.
                    try:
                        _connect_scope = anyio.CancelScope(
                            deadline=anyio.current_time() + BRIDGE_CONNECT_TIMEOUT_SECONDS
                        )
                        with _connect_scope:
                            async with streamablehttp_client(
                                remote_url,
                                httpx_client_factory=httpx_factory,
                            ) as (remote_read, remote_write, get_sid):
                                _connect_scope.deadline = math.inf
                                remote_write_slot.write = remote_write
                                try:
                                    supervisor_obj.remote_session_id = get_sid()
                                except Exception as exc:
                                    # get_sid() may not be implemented on every
                                    # transport; log so bridge diagnostics aren't
                                    # silently misleading when the field is null.
                                    log.debug("octowright.bridge.get_sid_failed", error=repr(exc))
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

                                    async def _remote_reader(reader: Any = remote_read) -> None:
                                        async for message in reader:
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
                        if _connect_scope.cancelled_caught:
                            raise TimeoutError(
                                f"connection to {remote_url!r} timed out after {BRIDGE_CONNECT_TIMEOUT_SECONDS}s"
                            )
                    except Exception as exc:
                        remote_write_slot.write = None
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
