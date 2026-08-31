# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import itertools
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCNotification, JSONRPCRequest
from provide.telemetry import get_logger

from octowright import defaults
from octowright._bridge_message_helpers import (
    BRIDGE_ERROR_CODE,  # noqa: F401 — re-exported for callers/tests
    BRIDGE_ERROR_GUIDANCE,  # noqa: F401
    BRIDGE_ERROR_PREFIX,  # noqa: F401
    bridge_error,
    is_request,
    is_response,
    message_method,
    message_request_id,
    message_root,
    message_tool_name,
)
from octowright._trace_propagation import build_tracing_http_client
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
_BRIDGE_SUSPENSION = counter(
    "octowright_bridge_suspension_total",
    description="Follower-process suspensions detected by the deadline watchdog (a client froze us, e.g. compaction)",
)

# A watch_deadlines iteration whose wall-clock gap exceeds its sleep interval by
# more than this is a process *suspension* (the MCP client SIGSTOPped the
# follower — e.g. Codex compaction freezing it), not normal scheduling jitter.
# The frozen time would otherwise blow monotonic-based in-flight deadlines and
# strand the now-stale leader session. (defaults.py is at its LOC ceiling.)
SUSPEND_THRESHOLD_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_SUSPEND_THRESHOLD_SECONDS", "5.0"))

# Reserved namespace for progressTokens the BRIDGE invents (see _inject_meta).
# A token bearing it is bridge-internal by construction and must never reach the
# local client, which never asked for progress and cannot resolve the token.
# Membership in `_synthetic_progress_tokens` is the fast path, but that set is
# deliberately torn down when a request finishes, so the prefix is the durable
# test — see forward_remote_message.
SYNTHETIC_PROGRESS_PREFIX = "owpt-"

log = get_logger(__name__)


@dataclass
class _RemoteWriteSlot:
    write: Any | None = None
    # Fired when the first remote writer is assigned; reset to a new Event on
    # each disconnect so the polling loop in forward_one_local_message always
    # waits on the *current* event, not a stale already-set one.
    ready: anyio.Event = field(default_factory=anyio.Event)


@dataclass
class _RemoteResetSlot:
    cancel_scope: Any | None = None


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


def bridge_http_client(headers: dict[str, str], supervisor_obj: Any) -> Any:
    """The follower's HTTP client for one leader connection.

    MCP 2.0 takes a ready-made client instead of a factory, and its transport no
    longer exposes ``get_session_id`` — so the session id is captured from the
    response header here and pushed onto the supervisor, where bridge state (and
    the leader's pid-liveness reaper) reads it.
    """
    supervisor_obj.remote_session_id = None

    def _note_session_id(value: str) -> None:
        supervisor_obj.remote_session_id = value

    return build_tracing_http_client(headers=headers, on_session_id=_note_session_id)


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
        # The notifications/initialized that follows the client's initialize. Cached
        # so a reconnect replays the FULL handshake — without it the fresh leader
        # session stays half-initialized and rejects calls with 400.
        self._initialized_message: SessionMessage | None = None
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
        self.suspensions = 0
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
            # Startup / reconnect race: poll until a live writer appears or the
            # connect timeout expires. We re-read `ready` from the slot on each
            # iteration because it's replaced with a fresh Event on each disconnect
            # (anyio.Event is one-shot; a stale set event would return immediately
            # even though write is still None, causing a spurious bridge error).
            deadline = anyio.current_time() + defaults.BRIDGE_CONNECT_TIMEOUT_SECONDS
            while remote_write_slot.write is None:
                remaining = deadline - anyio.current_time()
                if remaining <= 0:
                    break
                with anyio.move_on_after(min(remaining, 0.5)):
                    await remote_write_slot.ready.wait()
            remote_write = remote_write_slot.write
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
            remote_write_slot.ready = anyio.Event()  # reset so reconnect waiters pick up the next connection
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
            token = f"{SYNTHETIC_PROGRESS_PREFIX}{request_id}-{next(self._progress_token_counter)}"
            self._synthetic_progress_tokens.add(token)
            self._progress_tokens[token] = request_id
            meta["progressToken"] = token
        params["_meta"] = meta
        new_root = root.model_copy(update={"params": params})
        return token, key, SessionMessage(new_root)

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

    def _is_synthetic_progress_token(self, token: Any) -> bool:
        """Whether ``token`` is one the bridge invented rather than the client.

        The membership set alone is not enough: `_discard_progress_token` empties
        it the moment a request finishes, so a progress frame the leader emits
        after that (it has not learned of the follower's timeout, and its
        heartbeat keeps pinging) failed the test and was forwarded — handing the
        client a progressToken it never issued, for a request already errored.
        The prefix is reserved, so it stays true after the bookkeeping is gone.
        """
        if token in self._synthetic_progress_tokens:
            return True
        return isinstance(token, str) and token.startswith(SYNTHETIC_PROGRESS_PREFIX)

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
        if message_method(message) == "notifications/initialized":
            self._initialized_message = message
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
        replay_message = SessionMessage(replay_request)
        await remote_write.send(replay_message)
        # Complete the handshake on the fresh session: replay the cached
        # notifications/initialized too, or the leader leaves the session
        # half-initialized and 400s the next tool call.
        if self._initialized_message is not None:
            await remote_write.send(self._initialized_message)

    async def _forward_progress(self, message: SessionMessage, progress_token: Any) -> None:
        """Progress means the op is alive: re-arm its deadline. A bridge-synthetic
        token is swallowed (the client never asked for it); a client-supplied one
        is forwarded through unchanged."""
        self._rearm_deadline(progress_token)
        if self._is_synthetic_progress_token(progress_token):
            return
        await self.local_write.send(message)

    def _settle_in_flight(self, request_id: str | int, message: SessionMessage) -> bool:
        """Close out ``request_id``; return whether ``message`` may be forwarded.

        Every path that finishes a request early (deadline expiry, connection
        reset, stream close) POPS the entry and sends the client a synthetic
        bridge_error, spending the one response this id is allowed. So an id
        that is no longer here has already been answered, and a response for it
        would be a duplicate on the wire.

        The drop is gated on ``is_response``, NOT on "unknown id": the leader
        also sends the client genuine REQUESTS (sampling/createMessage,
        elicitation, roots/list) whose ids are its own and were never tracked
        here. Those must pass through untouched.
        """
        in_flight = self._in_flight.pop(request_id, None)
        if in_flight is None:
            return not is_response(message)
        if in_flight.responded:
            return False
        in_flight.responded = True
        self._discard_progress_token(in_flight)
        # End-to-end RPC latency: from when the follower forwarded the request
        # to when the matching response arrived from the leader. Outcome label
        # distinguishes success (JSONRPCResponse) from leader-side error.
        _BRIDGE_RPC_DURATION.record(
            time.monotonic() - in_flight.started_at,
            attributes={
                "method": in_flight.method or "unknown",
                "outcome": "error" if isinstance(message_root(message), JSONRPCError) else "ok",
            },
        )
        return True

    async def forward_remote_message(self, message: SessionMessage) -> None:
        progress_token = self._progress_token_of(message)
        if progress_token is not None:
            await self._forward_progress(message, progress_token)
            return
        request_id = message_request_id(message)
        if request_id is not None and request_id in self._internal_replay_ids:
            # Bridge-internal initialize replay: the local client has already
            # been told the session is initialized; forwarding a second
            # response would be a duplicate id from the client's perspective.
            self._internal_replay_ids.discard(request_id)
            return
        if request_id is not None and not self._settle_in_flight(request_id, message):
            return
        await self.local_write.send(message)

    def _handle_suspension(self, gap: float) -> None:
        """The follower process was suspended ~``gap`` seconds (a client froze us,
        e.g. Codex compaction SIGSTOPping the follower). In-flight deadlines are
        ``time.monotonic``-based, so the frozen time consumed them unfairly —
        shift each forward by ``gap`` so a request the freeze stranded isn't
        instantly failed when we resume.

        Deliberately does NOT force a reconnect: if the leader connection died
        during the freeze, the reactive path (a read/send error or the httpx2
        timeout → reset → resume on a freshly-handshaken session) reconnects on
        its own; if the connection survived, requests keep flowing. Forcing a
        reconnect here instead races the in-flight forward and strands the very
        call we're trying to protect."""
        self.suspensions += 1
        _BRIDGE_SUSPENSION.add(1)
        self.last_error = f"follower suspended ~{gap:.0f}s (client freeze); shifted in-flight deadlines"
        log.warning("octowright.bridge.follower_suspended", gap_seconds=round(gap, 1), in_flight=len(self._in_flight))
        for item in self._in_flight.values():
            item.deadline += gap

    async def watch_deadlines(
        self,
        interval: float = 0.1,
        reset_slot: _RemoteResetSlot | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        last = monotonic()
        while True:
            await sleep(interval)
            now = monotonic()
            gap = now - last
            last = now
            # A gap far exceeding our sleep interval means the process was
            # frozen (the MCP client suspended the follower). Don't fail the
            # in-flight requests the freeze stranded — shift their deadlines
            # instead (see _handle_suspension), and skip expiry this tick.
            if gap > interval + SUSPEND_THRESHOLD_SECONDS:
                self._handle_suspension(gap)
                continue
            await self._expire_overdue(now, reset_slot)

    async def _expire_overdue(self, now: float, reset_slot: _RemoteResetSlot | None) -> None:
        for item in [it for it in self._in_flight.values() if it.deadline <= now]:
            current = self._in_flight.pop(item.request_id, None)
            if current is None or current.responded:
                continue
            current.responded = True
            self._discard_progress_token(current)
            self.request_timeouts += 1
            self.last_error = f"request {current.request_id!r} timed out while waiting for leader response"
            # Record the full timeout duration so dashboards see the tail
            # latency, not just the success path.
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
