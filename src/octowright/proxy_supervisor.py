# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse
from provide.telemetry import get_logger

from octowright import defaults
from octowright._tracing import counter, histogram, span
from octowright.defaults import BRIDGE_TOOL_TIMEOUTS

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
_BRIDGE_RESUME = counter(
    "octowright_bridge_resume_total",
    description="In-flight requests re-sent to the leader after a reconnect (idempotent resume)",
)

BRIDGE_ERROR_CODE = -32000
BRIDGE_ERROR_PREFIX = "Octowright bridge error:"
# Appended to every bridge error so the agent that receives it on a failed in-flight
# call is steered away from the observed failure mode: silently substituting a
# shell-opened browser (`open`/`xdg-open`/`start`) and reporting it as launched. A
# fully-dead leader can't send any message, so the skill + MCP server instructions
# carry the same guidance for that case; this covers the recoverable/timeout path.
BRIDGE_ERROR_GUIDANCE = (
    "This is an Octowright transport error, not a browser result. Retry ONE call; if it "
    "still fails, Octowright is disconnected — STOP and tell the user to reconnect it. "
    "Forbidden: running 'octowright restart' or 'which octowright' via shell (binary not on "
    "agent PATH; restarting the daemon closes the MCP connection, not fixes it); probing "
    "/api/health; opening URLs with shell commands (open/xdg-open/start); writing Playwright "
    "scripts as a fallback. None of these restore the MCP connection. Only the user can "
    "reconnect the MCP client. Claude Code: /mcp -> select octowright -> Reconnect. "
    "Other clients: ask which client, have them use its MCP reconnect control or restart it."
)

log = get_logger(__name__)


@dataclass
class _RemoteWriteSlot:
    write: Any | None = None


@dataclass
class _RemoteResetSlot:
    cancel_scope: Any | None = None


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


def message_tool_name(message: SessionMessage) -> str | None:
    """Return the tool name of a ``tools/call`` request (its ``params.name``), else None.

    Lets the bridge apply a per-tool in-flight deadline: the JSON-RPC ``method`` is
    always ``tools/call``, so the discriminating identity is the tool name in params.
    """
    root = message_root(message)
    if isinstance(root, JSONRPCRequest) and root.method == "tools/call":
        params = root.params
        if isinstance(params, dict):
            name = params.get("name")
            if isinstance(name, str):
                return name
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
                    message=f"{BRIDGE_ERROR_PREFIX} {reason} {BRIDGE_ERROR_GUIDANCE}",
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
    # Per-tool deadline budget, re-applied whenever a progress notification
    # re-arms the deadline (see ``_rearm_deadline``).
    timeout: float = 0.0
    # progressToken (client-supplied or bridge-synthetic) tied to this request so
    # a leader progress notification can be matched back to re-arm the deadline.
    progress_token: Any = None
    # The exact frame forwarded to the leader, with bridge ``_meta`` injected
    # (progressToken + idempotency key). Stored so a reconnect can re-send it
    # verbatim — same id, same token, same key — for safe resume.
    outgoing: SessionMessage | None = None
    # Stable per-request idempotency key (``owk-<uuid4>``) injected into _meta and
    # reused on every resume so the leader dedups a re-sent side-effectful call.
    idempotency_key: str | None = None
    # Number of times this request has been re-sent on a fresh leader session
    # after a reconnect; bounded by BRIDGE_RESUME_MAX_ATTEMPTS.
    resume_count: int = 0
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
        # Progress-token bookkeeping: every in-flight progressToken (client or
        # bridge-synthetic) maps to its request id so a leader progress
        # notification re-arms the right deadline; the synthetic subset is also
        # swallowed (never forwarded), since the client never asked for it.
        self._progress_tokens: dict[Any, str | int] = {}
        self._synthetic_progress_tokens: set[Any] = set()
        self._progress_token_counter = itertools.count(1)
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
                await remote_write.send(self._outgoing_frame(request_id, message))
            except Exception as exc:
                await self._handle_forward_failure(message, request_id, method, remote_write, remote_write_slot, exc)

    def _outgoing_frame(self, request_id: str | int | None, message: SessionMessage) -> SessionMessage:
        """The frame to forward: the tracked one (bridge ``_meta`` injected) when
        this is a tracked request, else the original (notifications)."""
        if request_id is not None:
            tracked = self._in_flight.get(request_id)
            if tracked is not None and tracked.outgoing is not None:
                return tracked.outgoing
        return message

    async def _handle_forward_failure(
        self,
        message: SessionMessage,
        request_id: str | int | None,
        method: str,
        remote_write: Any,
        remote_write_slot: _RemoteWriteSlot,
        exc: Exception,
    ) -> None:
        """A failed outbound send: log a dropped notification, drop the stale writer
        (only if unchanged), and fail/resume the in-flight requests."""
        # A failed notification has nowhere to return an error — log it so a missing
        # notifications/* round-trip (e.g. notifications/initialized after reconnect)
        # is at least visible in post-mortem.
        if not (is_request(message) and request_id is not None):
            log.debug("octowright.bridge.notification_drop", method=method, error=repr(exc))
        # Only clear the slot if it still holds the writer we just tried to send
        # through: the remote supervisor may have reconnected and swapped in a fresh
        # writer during the await, and an unconditional clear would nuke it.
        if remote_write_slot.write is remote_write:
            remote_write_slot.write = None
        # Keep resumable keyed requests in-flight (the reconnect re-sends them); fail
        # only the non-resumable ones, so one send failure doesn't nuke unrelated work.
        await self.fail_or_mark_for_resume("leader session unavailable; retry")

    def _timeout_for(self, message: SessionMessage) -> float:
        """In-flight deadline budget for ``message``.

        Long-running tools (browser_launch, macro_run) get a larger floor from
        ``BRIDGE_TOOL_TIMEOUTS`` so they don't hit the flat request timeout while
        the leader is still working; everything else uses the flat default the
        supervisor was constructed with.
        """
        tool = message_tool_name(message)
        if tool is not None:
            return BRIDGE_TOOL_TIMEOUTS.get(tool, self.request_timeout_seconds)
        return self.request_timeout_seconds

    def _inject_meta(self, message: SessionMessage, request_id: str | int) -> tuple[Any, str | None, SessionMessage]:
        """Rewrite an outgoing ``tools/call`` _meta; return
        ``(progress_token, idempotency_key, outgoing_message)``.

        Two injections:
        - **idempotency key** (``owk-<uuid4>``) — always added, stable per logical
          request, reused verbatim on resume so the leader can dedup a re-sent
          side-effectful call instead of double-running it.
        - **progressToken** — the client's is kept (and its progress forwarded);
          otherwise a synthetic one is injected so the leader streams progress we
          use only to re-arm the deadline, swallowing it on the way back.
        """
        root = message_root(message)
        if not isinstance(root, JSONRPCRequest):
            return None, None, message
        params = dict(root.params) if isinstance(root.params, dict) else {}
        meta = dict(params.get("_meta") or {})
        # Idempotency key only when enabled, so the kill switch restores today's
        # exact wire format. The progressToken below is independent of idempotency.
        key: str | None = None
        if defaults.IDEMPOTENCY_ENABLED:
            key = f"owk-{uuid4().hex}"
            meta["octowrightIdempotencyKey"] = key
        client_token = meta.get("progressToken")
        if client_token is not None:
            token = client_token
            self._progress_tokens[client_token] = request_id
        else:
            token = f"owpt-{request_id}-{next(self._progress_token_counter)}"
            self._synthetic_progress_tokens.add(token)
            self._progress_tokens[token] = request_id
            meta["progressToken"] = token
        params["_meta"] = meta
        new_root = root.model_copy(update={"params": params})
        return token, key, SessionMessage(JSONRPCMessage(root=new_root))

    def _progress_token_of(self, message: SessionMessage) -> Any:
        """The progressToken of a ``notifications/progress`` frame, else None."""
        root = message_root(message)
        if isinstance(root, JSONRPCNotification) and root.method == "notifications/progress":
            params = root.params
            if isinstance(params, dict):
                return params.get("progressToken")
        return None

    def _rearm_deadline(self, token: Any) -> None:
        """Push out the deadline of the request owning ``token`` — progress means
        the op is alive, so it shouldn't be killed by the flat timeout."""
        request_id = self._progress_tokens.get(token)
        if request_id is None:
            return
        in_flight = self._in_flight.get(request_id)
        if in_flight is not None and not in_flight.responded:
            in_flight.deadline = time.monotonic() + (in_flight.timeout or self.request_timeout_seconds)

    def _discard_progress_token(self, in_flight: InFlightRequest) -> None:
        """Drop a finished request's progressToken bookkeeping so the maps don't
        grow without bound over a long-lived follower."""
        token = in_flight.progress_token
        if token is not None:
            self._progress_tokens.pop(token, None)
            self._synthetic_progress_tokens.discard(token)

    def track_local_message(self, message: SessionMessage) -> None:
        request_id = message_request_id(message)
        if is_request(message) and message_method(message) == "initialize":
            self._initialize_message = message
        if is_request(message) and request_id is not None:
            now = time.monotonic()
            timeout = self._timeout_for(message)
            progress_token: Any = None
            idempotency_key: str | None = None
            outgoing = message
            # tools/call frames get bridge _meta injected (a progressToken so the
            # leader streams progress that re-arms the deadline, and an idempotency
            # key so a re-sent call dedups). The injected frame is stored as
            # ``outgoing`` so a reconnect can re-send it verbatim.
            if message_tool_name(message) is not None:
                progress_token, idempotency_key, outgoing = self._inject_meta(message, request_id)
            self._in_flight[request_id] = InFlightRequest(
                request_id=request_id,
                method=message_method(message),
                started_at=now,
                deadline=now + timeout,
                timeout=timeout,
                progress_token=progress_token,
                idempotency_key=idempotency_key,
                outgoing=outgoing,
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
        progress_token = self._progress_token_of(message)
        if progress_token is not None:
            # Progress means the op is alive: re-arm its deadline. A bridge-
            # synthetic token is swallowed (the client never asked for it); a
            # client-supplied token is forwarded through unchanged.
            self._rearm_deadline(progress_token)
            if progress_token in self._synthetic_progress_tokens:
                return
            await self.local_write.send(message)
            return
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
                self._discard_progress_token(in_flight)
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

    async def watch_deadlines(self, interval: float = 0.1, reset_slot: _RemoteResetSlot | None = None) -> None:
        while True:
            await anyio.sleep(interval)
            now = time.monotonic()
            expired = [item for item in self._in_flight.values() if item.deadline <= now]
            for item in expired:
                current = self._in_flight.pop(item.request_id, None)
                if current is None or current.responded:
                    continue
                current.responded = True
                self._discard_progress_token(current)
                self.request_timeouts += 1
                self.last_error = f"request {current.request_id!r} timed out while waiting for leader response"
                # Record the full timeout duration so dashboards see the
                # tail latency, not just the success path.
                _BRIDGE_RPC_DURATION.record(
                    now - current.started_at,
                    attributes={"method": current.method or "unknown", "outcome": "timeout"},
                )
                await self.local_write.send(bridge_error(current.request_id, self.last_error))
                if reset_slot is not None and reset_slot.cancel_scope is not None:
                    reset_slot.cancel_scope.cancel()

    async def fail_all_in_flight(self, reason: str) -> None:
        pending = list(self._in_flight.values())
        self._in_flight.clear()
        self.last_error = reason
        now = time.monotonic()
        for item in pending:
            if item.responded:
                continue
            item.responded = True
            self._discard_progress_token(item)
            _BRIDGE_RPC_DURATION.record(
                now - item.started_at,
                attributes={"method": item.method or "unknown", "outcome": "failure"},
            )
            await self.local_write.send(bridge_error(item.request_id, reason))

    def _is_resumable(self, item: InFlightRequest) -> bool:
        """A keyed tools/call with resume budget left can be safely re-sent: the
        leader dedups on its idempotency key, so a re-send won't double-execute."""
        return (
            defaults.IDEMPOTENCY_ENABLED
            and item.idempotency_key is not None
            and item.outgoing is not None
            and item.resume_count < defaults.BRIDGE_RESUME_MAX_ATTEMPTS
        )

    async def fail_or_mark_for_resume(self, reason: str) -> None:
        """On a connection reset: keep resumable keyed requests in-flight (the
        success path re-sends them on the fresh session) and fail the rest with the
        retry-hint. With idempotency disabled, nothing is resumable so this degrades
        to failing everything — today's fail-safe behaviour.
        """
        self.last_error = reason
        now = time.monotonic()
        for item in list(self._in_flight.values()):
            if item.responded or self._is_resumable(item):
                continue  # resumable ones stay in-flight for resume_in_flight()
            item.responded = True
            self._discard_progress_token(item)
            self._in_flight.pop(item.request_id, None)
            _BRIDGE_RPC_DURATION.record(
                now - item.started_at,
                attributes={"method": item.method or "unknown", "outcome": "failure"},
            )
            await self.local_write.send(bridge_error(item.request_id, reason))

    async def resume_in_flight(self, remote_write: Any) -> None:
        """Re-send still-in-flight resumable requests on a freshly-reconnected
        session, re-arming each deadline. The leader dedups on the idempotency key,
        so a re-sent side-effectful call returns its cached result instead of
        running twice. Called on the success path right after ``replay_initialize``.
        """
        now = time.monotonic()
        for item in list(self._in_flight.values()):
            if item.responded or not self._is_resumable(item) or item.outgoing is None:
                continue
            item.resume_count += 1
            item.deadline = now + (item.timeout or self.request_timeout_seconds)
            _BRIDGE_RESUME.add(1, attributes={"method": item.method or "unknown"})
            try:
                await remote_write.send(item.outgoing)
            except Exception as exc:
                # The fresh session died again mid-resume; leave it in-flight for the
                # next reconnect cycle (or eventual budget exhaustion).
                log.debug("octowright.bridge.resume_failed", request_id=item.request_id, error=repr(exc))
                return
