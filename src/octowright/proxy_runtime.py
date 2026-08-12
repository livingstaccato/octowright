# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Follower-bridge connection runtime: leader URL resolution, reconnect backoff,
health monitoring, and the supervised stdio<->HTTP-MCP proxy loop.

Split out of ``proxy_supervisor`` (which holds the per-message ``BridgeSupervisor``
state machine) so each module stays focused. These functions reference each other
via module globals, so tests monkeypatch them here (``proxy_runtime.X``).
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import anyio
import httpx
from mcp.client.streamable_http import streamable_http_client
from mcp.server.stdio import stdio_server
from provide.telemetry import get_logger

from octowright import bridge_state, singleton
from octowright._tracing import counter
from octowright.defaults import (
    BRIDGE_CONNECT_TIMEOUT_SECONDS,
    BRIDGE_RECONNECT_MAX_SECONDS,
    BRIDGE_REQUEST_TIMEOUT_SECONDS,
    BRIDGE_STATE_PATH,
    FOLLOWER_EXIT_BACKSTOP_SECONDS,
)
from octowright.proxy_supervisor import (
    _BRIDGE_RECONNECT,
    BridgeSupervisor,
    _RemoteResetSlot,
    _RemoteWriteSlot,
    bridge_http_client,
)

log = get_logger(__name__)

# Outcome of a leader-down gap (noop unless telemetry is on): `recovered` = the
# follower reconnected to a (restarted) leader within the window and kept the
# session; `exhausted` = the leader stayed gone past the window so the follower
# exited. A field-wide recovered:exhausted ratio shows how often `octowright
# restart` is survived transparently vs. drops the client.
_LEADER_RECOVERY = counter(
    "octowright_bridge_leader_recovery_total",
    description="Leader-down gaps by outcome (recovered|exhausted)",
)

# How long the follower keeps retrying an unresponsive leader before it gives up and
# EXITS — closing the client's stdio (its MCP session dies until reconnect) and
# letting serve.py respawn a leader. Direct cause of "octowright breaks across ALL
# clients on a restart": the old 15s default was shorter than a real leader outage
# (`octowright restart` alone takes 20-30s+ — SIGTERM grace + wait-for-port-free +
# spawn + health), so every follower exited at once. 180s outlasts a normal restart,
# so followers WAIT it out and reconnect (replaying initialize) — sessions survive.
# Trade: a truly-gone leader takes this long before a follower respawns one; tunable.
BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", "180"))


def _within_recovery_window(leader_down_since: float | None, now: float, window: float) -> bool:
    """True while a still-unreachable leader is inside its recovery window (keep
    retrying the reconnect); False once it has elapsed (give up so the follower
    exits). ``leader_down_since`` is when the leader was first seen unreachable in
    the current gap; ``None`` means it has not been stamped yet (treat as inside)."""
    if leader_down_since is None:
        return True
    return (now - leader_down_since) < window


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


def resolve_leader_token() -> str:
    """The bridge capability token from the 0600 lockfile, for the
    ``X-Octowright-Token`` header the follower presents to the leader's /mcp.

    Returned only when the lock's URL passes the same loopback gate as
    ``resolve_leader_url`` — never hand the token to a rejected/remote URL, so a
    poisoned lock that redirects the follower can't also harvest the token. ``""``
    when there is no live lock or the lock predates the token (back-compat).
    """
    info = singleton.read_lock()
    if info is not None and not singleton.is_stale(info) and _leader_url_is_safe(info.mcp_url):
        return info.token
    return ""


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


# A session that lived shorter than this is a "flap": the leader accepted then
# almost-immediately ended it. Reconnecting a flap with no backoff busy-loops the
# leader into a create/terminate storm (observed ~300+/sec across followers); a
# session that lived at least this long reconnects promptly. (defaults.py at LOC ceiling.)
BRIDGE_MIN_SESSION_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_MIN_SESSION_SECONDS", "2.0"))


def _post_session_backoff(session_seconds: float, flap_attempt: int) -> tuple[float, int]:
    """After a session end, return ``(sleep_seconds, next_flap_attempt)``.

    A session shorter than ``BRIDGE_MIN_SESSION_SECONDS`` is a flap → back off by
    ``reconnect_delay(flap_attempt)`` so a leader that instantly ends sessions
    can't be hot-looped into a create/terminate storm. A healthy-length session
    reconnects immediately (0 delay) and resets the flap counter."""
    if session_seconds < BRIDGE_MIN_SESSION_SECONDS:
        flap_attempt += 1
        return reconnect_delay(flap_attempt, max_delay=BRIDGE_RECONNECT_MAX_SECONDS), flap_attempt
    return 0.0, 0


async def _flap_backoff(connected_at: float | None, flap_attempt: int) -> tuple[int, bool]:
    """If a session OPENED then ended too fast, sleep a flap backoff and return
    ``(next_flap_attempt, True)``; else ``(flap_attempt, False)``. Used on BOTH the
    success path (clean instant end) and the error path (connect-then-abort /
    ClientDisconnect) — the latter matters because its ``attempt`` counter resets on
    each connect, so the (only-grows) flap counter is what actually throttles it."""
    if connected_at is None:
        return flap_attempt, False
    session_seconds = anyio.current_time() - connected_at
    delay, flap_attempt = _post_session_backoff(session_seconds, flap_attempt)
    if delay <= 0:
        return flap_attempt, False
    _BRIDGE_RECONNECT.add(1, attributes={"reason": "session_flap"})
    log.warning(
        "octowright.bridge.session_flap",
        session_seconds=round(session_seconds, 3),
        flap_attempt=flap_attempt,
        backoff_seconds=round(delay, 3),
    )
    await anyio.sleep(delay)
    return flap_attempt, True


def _arm_follower_exit_backstop(
    grace_seconds: float, *, exit_fn: Callable[[int], object] = os._exit
) -> threading.Timer:
    """Force the follower to exit if graceful bridge teardown wedges.

    Once the MCP client closes stdin it's gone, so the bridge cancels itself —
    but the remote SSE read can block in the transport and ignore anyio
    cancellation, which would leave the follower running forever (bridging to
    whatever leader is in the lockfile) long after its client. A daemon-thread
    timer guarantees exit after ``grace_seconds``. The bridge owns no state, so
    ``os._exit`` is safe. If the normal shutdown wins the race, the process is
    already gone and the daemon timer is silently abandoned.
    """
    timer = threading.Timer(grace_seconds, lambda: exit_fn(0))
    timer.daemon = True
    timer.start()
    return timer


async def monitor_leader_health(
    health_url: str,
    interval: float,
    max_failures: int,
    on_unhealthy: Callable[[], None],
) -> None:
    """Probe the leader's HTTP health; after ``max_failures`` consecutive misses,
    call ``on_unhealthy`` — which UNSTICKS the current remote connection so the
    inline reconnect loop can act. It does NOT tear the follower down: a SIGKILL'd
    leader's SSE read can hang silently (no close, no error), leaving the inline
    loop stuck; this is the only thing that breaks it out so the loop can reconnect
    to a respawned leader (instead of wedging until the recovery window expires).
    The inline windowed retry remains the sole authority on giving up. Keeps
    watching (re-fires) so a still-silent connection is unstuck again."""
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
                on_unhealthy()
                failures = 0


def _events_url_from_mcp(mcp_url: str) -> str:
    """The leader's ``/api/mcp-events`` SSE URL derived from its ``/mcp`` URL —
    same host:port, mirroring how the health URL is derived in ``_run_follower``."""
    return mcp_url.rsplit("/mcp", 1)[0] + "/api/mcp-events"


async def consume_leader_notifications(
    fallback_mcp_url: str,
    local_write: Any,
    *,
    sleep: Callable[[float], Any] = anyio.sleep,
) -> None:
    """Stream the leader's ``/api/mcp-events`` SSE and inject each MCP notification
    into the local stdio client write.

    This is what makes proactive notifications (browser_crashed / browser_recovered
    / driver_died / session_closed) reach the client in the default detached-daemon
    deployment: the leader's HTTP-MCP transport delivers no server-initiated
    notifications, so the follower re-sources them from the leader's session-event
    SSE and writes them onto the same stdio stream the client reads. The leader's
    own stdio emitter writes to the detached daemon's (clientless) stdout, so there
    is no double-delivery.

    Reconnects with the same backoff as the RPC bridge; a re-resolved leader URL
    each attempt picks up a restarted leader's new port. Runs until cancelled.
    """
    cancelled = anyio.get_cancelled_exc_class()
    # No read timeout (SSE is long-lived); bound only the connect handshake so a
    # dead leader fails fast into the reconnect backoff instead of hanging.
    timeout = httpx.Timeout(None, connect=BRIDGE_CONNECT_TIMEOUT_SECONDS)
    attempt = 0
    while True:
        events_url = _events_url_from_mcp(resolve_leader_url(fallback_mcp_url))
        # Present the same capability token the follower uses on /mcp — the
        # leader gates /api/mcp-events with it (see http/routes/mcp_events).
        token = resolve_leader_token()
        headers = {"X-Octowright-Token": token} if token else {}
        try:
            async with (
                httpx.AsyncClient(timeout=timeout) as client,
                client.stream("GET", events_url, headers=headers) as response,
            ):
                if response.status_code != 200:
                    raise RuntimeError(f"mcp-events stream returned HTTP {response.status_code}")
                attempt = 0  # connected → reset backoff
                await _forward_sse_notifications(response.aiter_lines(), local_write)
        except cancelled:
            raise
        except Exception as exc:
            log.debug("octowright.bridge.notif_stream_error", error=repr(exc))
        await sleep(reconnect_delay(attempt, max_delay=BRIDGE_RECONNECT_MAX_SECONDS))
        attempt += 1


async def _forward_sse_notifications(lines: Any, local_write: Any) -> None:
    """Parse an async iterator of SSE lines and inject each JSON-RPC notification
    payload into ``local_write``.

    Skips SSE comments (``: heartbeat`` / ``: ready``), blank separators, and
    malformed ``data:`` frames. A send failure (the local client closed) is
    swallowed — the RPC bridge owns teardown — so one dead write doesn't kill the
    stream. Extracted from ``consume_leader_notifications`` so the parse/deliver
    contract is unit-testable without a live HTTP stream.
    """
    from octowright.server.mcp_notifications import payload_to_message

    async for line in lines:
        if not line.startswith("data:"):
            continue
        raw = line[len("data:") :].strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "method" in payload and "params" in payload:
            with suppress(Exception):
                await local_write.send(payload_to_message(payload))


async def leader_health_alive(health_url: str) -> bool:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(health_url)
        except (httpx.HTTPError, OSError):
            return False
    return response.status_code == 200


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
            remote_reset_slot = _RemoteResetSlot()
            leader_health_failed = False
            # When the leader was first seen unreachable in the current gap (None =
            # reachable). A 1-element list so BOTH the inline reconnect loop AND the
            # background health monitor's unstick can stamp it — a hard-killed
            # leader's outage is observed by the monitor (silent SSE), and stamping
            # here lets the eventual reconnect still be metered as a recovery.
            leader_down: list[float | None] = [None]

            def _mark_leader_health_failed() -> None:
                nonlocal leader_health_failed
                leader_health_failed = True

            async def _local_forwarder() -> None:
                async for message in local_read:
                    await supervisor_obj.forward_one_local_message(message, remote_write_slot)
                # stdin EOF: the MCP client is gone. Cancel the bridge and arm a
                # hard-exit backstop so a wedged remote teardown can't keep the
                # follower alive past its client (the orphaned-follower leak).
                local_tg.cancel_scope.cancel()
                _arm_follower_exit_backstop(FOLLOWER_EXIT_BACKSTOP_SECONDS)

            async def _remote_supervisor() -> None:
                attempt = 0

                async def _leader_recoverable() -> bool:
                    """True → keep retrying the leader; False → give up so the
                    follower exits and serve.py respawns. A leader unreachable
                    longer than BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS is gone for
                    good; a briefly-down one (restart) is retried so the client's
                    session survives. health_url=None disables the watchdog. Does
                    NOT clear ``leader_down`` on a healthy probe — only a successful
                    reconnect does, so an outage seen here OR by the monitor is still
                    metered as a recovery when the reconnect lands."""
                    if health_url is None:
                        return True
                    if await leader_health_alive(health_url):
                        return True
                    now = anyio.current_time()
                    down_at = leader_down[0]
                    if down_at is None:
                        down_at = now
                        leader_down[0] = now
                        log.warning(
                            "octowright.bridge.leader_unreachable_retrying",
                            recovery_window_s=BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS,
                        )
                    if _within_recovery_window(down_at, now, BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS):
                        return True
                    log.warning("octowright.bridge.leader_recovery_window_exhausted", waited_s=round(now - down_at, 1))
                    _LEADER_RECOVERY.add(1, attributes={"outcome": "exhausted"})
                    return False

                flap_attempt = 0
                while True:
                    # When this iteration's session became live (None until the
                    # stream opens). Used to detect a flap — a session that ends
                    # almost immediately — on the success path below.
                    connected_at: float | None = None
                    remote_url = resolve_leader_url(leader_mcp_url)
                    # Present the capability token from the 0600 lockfile so the
                    # leader's /mcp guard admits us. Re-read each connect so a
                    # restarted leader's fresh token is picked up.
                    remote_token = resolve_leader_token()
                    # Self-identify so the leader's per-source new-session rate limit
                    # buckets THIS follower (old followers share "anonymous"). See mcp_flap_guard.
                    remote_headers = {"X-Octowright-Follower": str(os.getpid())}
                    if remote_token:
                        remote_headers["X-Octowright-Token"] = remote_token
                    # async with (not manual __aenter__/__aexit__) so the CM's async
                    # generator enters+exits in one coroutine — Python 3.13 finalizes
                    # abandoned async generators in a separate task, and anyio cancel
                    # scopes can't span tasks ("exit cancel scope in a different task").
                    # CancelScope+deadline scopes the timeout to the connect handshake;
                    # inside the block the deadline goes to infinity so long-running
                    # sessions aren't killed.
                    try:
                        # Build the client BEFORE arming the deadline: the first
                        # httpx2 client in a process pays ~65ms for its SSL
                        # context and CA bundle, and that is setup, not connect
                        # time. Arming first spent part of the connect budget
                        # before a single packet moved.
                        http_client = bridge_http_client(remote_headers, supervisor_obj)
                        _connect_scope = anyio.CancelScope(
                            deadline=anyio.current_time() + BRIDGE_CONNECT_TIMEOUT_SECONDS
                        )
                        with _connect_scope:
                            async with (
                                http_client,
                                streamable_http_client(
                                    remote_url,
                                    http_client=http_client,
                                ) as (remote_read, remote_write),
                            ):
                                _connect_scope.deadline = math.inf
                                connected_at = anyio.current_time()
                                remote_write_slot.write = remote_write
                                remote_write_slot.ready.set()
                                supervisor_obj.reconnect_attempts = attempt
                                await bridge_state.record_snapshot_async(
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
                                # Re-send any in-flight requests kept across the
                                # reconnect (idempotent resume) on this fresh session.
                                await supervisor_obj.resume_in_flight(remote_write)
                                attempt = 0
                                # Connected → the leader is back. If an outage was
                                # observed (inline OR by the monitor's unstick), this
                                # reconnect survived it — meter the recovery, then
                                # clear the clock (only a real reconnect clears it).
                                if leader_down[0] is not None:
                                    _LEADER_RECOVERY.add(1, attributes={"outcome": "recovered"})
                                leader_down[0] = None
                                async with anyio.create_task_group() as remote_tg:
                                    remote_reset_slot.cancel_scope = remote_tg.cancel_scope

                                    async def _remote_reader(reader: Any = remote_read) -> None:
                                        try:
                                            async for message in reader:
                                                if isinstance(message, Exception):
                                                    raise message
                                                await supervisor_obj.forward_remote_message(message)
                                        finally:
                                            remote_tg.cancel_scope.cancel()

                                    remote_tg.start_soon(_remote_reader)
                                remote_reset_slot.cancel_scope = None
                        if _connect_scope.cancelled_caught:
                            raise TimeoutError(
                                f"connection to {remote_url!r} timed out after {BRIDGE_CONNECT_TIMEOUT_SECONDS}s"
                            )
                        if not await _leader_recoverable():
                            _mark_leader_health_failed()
                            local_tg.cancel_scope.cancel()
                            return
                        # Flap guard (success path): throttle a clean instant session.
                        flap_attempt, _ = await _flap_backoff(connected_at, flap_attempt)
                    except Exception as exc:
                        remote_write_slot.write = None
                        remote_write_slot.ready = (
                            anyio.Event()
                        )  # one-shot reset so reconnect waiters pick up the next connection
                        _BRIDGE_RECONNECT.add(1, attributes={"reason": type(exc).__name__})
                        await supervisor_obj.fail_or_mark_for_resume(f"remote leader session reset: {exc!r}")
                        supervisor_obj.last_error = repr(exc)
                        await bridge_state.record_snapshot_async(
                            path=BRIDGE_STATE_PATH,
                            follower_pid=__import__("os").getpid(),
                            remote_url=remote_url,
                            remote_session_id=supervisor_obj.remote_session_id,
                            last_error=supervisor_obj.last_error,
                            in_flight=supervisor_obj.in_flight_count,
                            reconnect_attempts=attempt,
                            request_timeouts=supervisor_obj.request_timeouts,
                        )
                        if not await _leader_recoverable():
                            _mark_leader_health_failed()
                            local_tg.cancel_scope.cancel()
                            return
                        # A connect-then-abort (ClientDisconnect) is a flap here too;
                        # throttle by the flap counter. A genuine connect failure
                        # (session never opened) falls through to attempt backoff.
                        flap_attempt, flapped = await _flap_backoff(connected_at, flap_attempt)
                        if not flapped:
                            await anyio.sleep(reconnect_delay(attempt, max_delay=BRIDGE_RECONNECT_MAX_SECONDS))
                            attempt += 1

            local_tg.start_soon(_local_forwarder)
            local_tg.start_soon(_remote_supervisor)
            local_tg.start_soon(supervisor_obj.watch_deadlines, 0.1, remote_reset_slot)
            # Re-source the leader's proactive MCP notifications (crash / driver-died
            # / session-closed) over its /api/mcp-events SSE and inject them into the
            # local client write — the HTTP-MCP transport the leader serves delivers
            # no server-initiated notifications, so without this the daemon-mode
            # client never sees them (see http/routes/mcp_events.py).
            local_tg.start_soon(consume_leader_notifications, leader_mcp_url, supervisor_obj.local_write)
            if health_url is not None:
                # The background monitor catches a leader whose SSE read goes
                # SILENT (a hard crash: no close, no error). It cancels the stuck
                # remote connection so the inline reconnect loop can act — it does
                # NOT tear the follower down (the inline windowed retry owns
                # give-up). So it fires on its own small threshold and is harmless
                # if it fires during a restart the inline loop is reconnecting
                # through: it just nudges the same reconnect along.
                def _unstick_current_remote() -> None:
                    # The leader's HTTP is unreachable — record the outage start so
                    # the reconnect this triggers is metered as a recovery, then
                    # cancel the stuck connection so the inline loop reconnects.
                    if leader_down[0] is None:
                        leader_down[0] = anyio.current_time()
                    scope = remote_reset_slot.cancel_scope
                    if scope is not None:
                        scope.cancel()

                local_tg.start_soon(
                    monitor_leader_health,
                    health_url,
                    heartbeat_interval,
                    heartbeat_max_failures,
                    _unstick_current_remote,
                )
        if leader_health_failed:
            raise RuntimeError("leader health check failed")
