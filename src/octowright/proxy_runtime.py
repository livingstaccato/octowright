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

import math
import os
import threading
from collections.abc import Callable
from typing import Any

import anyio
import httpx
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.stdio import stdio_server
from provide.telemetry import get_logger

from octowright import bridge_state, singleton
from octowright._trace_propagation import tracing_httpx_client_factory
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

# How long the follower keeps retrying a leader that's stopped answering before it
# gives up and exits (so serve.py can respawn). A leader restart / respawn brings a
# new leader up in ~1-2s; tolerating a window means `octowright restart` no longer
# kills connected sessions — the bridge reconnects to the new leader (replaying
# initialize) and the client's MCP session survives transparently. A leader gone
# longer than this is treated as gone for good. Lives here (not defaults.py, at its
# LOC ceiling), mirroring incidents/health. 0 = no grace (immediate exit, legacy).
BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS = float(os.environ.get("OCTOWRIGHT_BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", "15"))


def _within_recovery_window(leader_down_since: float | None, now: float, window: float) -> bool:
    """True while a still-unreachable leader is inside its recovery window (keep
    retrying the reconnect); False once it has elapsed (give up so the follower
    exits). ``leader_down_since`` is when the leader was first seen unreachable in
    the current gap; ``None`` means it has not been stamped yet (treat as inside)."""
    if leader_down_since is None:
        return True
    return (now - leader_down_since) < window


def _monitor_max_failures_for_window(base: int, interval: float, window: float) -> int:
    """Consecutive-failure count the background health monitor must reach before
    tearing the follower down, raised so its tolerance covers the inline reconnect
    recovery window. Without this the monitor (a few failures at its interval) would
    kill a follower the inline loop is still legitimately reconnecting through a
    leader restart. ``interval<=0`` is a guard — fall back to ``base``."""
    if interval <= 0:
        return base
    return max(base, math.ceil(window / interval))


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
    cancel_scope: anyio.CancelScope,
    health_url: str,
    interval: float,
    max_failures: int,
    on_failure: Callable[[], None] | None = None,
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
                if on_failure is not None:
                    on_failure()
                cancel_scope.cancel()
                return


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
                # When the leader was first seen unreachable in the current gap
                # (None = reachable). Drives the bounded recovery window so a
                # restart/respawn is retried through instead of killing the follower.
                leader_down_since: float | None = None
                # Build the tracing httpx factory once; it's a closure with no
                # per-connection state, so reusing it across reconnects avoids
                # an allocation per attempt.
                httpx_factory = tracing_httpx_client_factory()

                async def _leader_recoverable() -> bool:
                    """True → keep retrying the leader; False → give up so the
                    follower exits and serve.py respawns. A leader unreachable
                    longer than BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS is gone for
                    good; a briefly-down one (restart) is retried so the client's
                    session survives. health_url=None disables the watchdog."""
                    nonlocal leader_down_since
                    if health_url is None:
                        return True
                    if await leader_health_alive(health_url):
                        leader_down_since = None
                        return True
                    now = anyio.current_time()
                    if leader_down_since is None:
                        leader_down_since = now
                        log.warning(
                            "octowright.bridge.leader_unreachable_retrying",
                            recovery_window_s=BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS,
                        )
                    if _within_recovery_window(leader_down_since, now, BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS):
                        return True
                    log.warning(
                        "octowright.bridge.leader_recovery_window_exhausted", waited_s=round(now - leader_down_since, 1)
                    )
                    _LEADER_RECOVERY.add(1, attributes={"outcome": "exhausted"})
                    return False

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
                                remote_write_slot.ready.set()
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
                                # Re-send any in-flight requests kept across the
                                # reconnect (idempotent resume) on this fresh session.
                                await supervisor_obj.resume_in_flight(remote_write)
                                attempt = 0
                                # Connected → the leader is back. If we were in a
                                # down-gap, this reconnect just survived it (e.g. an
                                # `octowright restart`) — meter the recovery.
                                if leader_down_since is not None:
                                    _LEADER_RECOVERY.add(1, attributes={"outcome": "recovered"})
                                leader_down_since = None
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
                    except Exception as exc:
                        remote_write_slot.write = None
                        remote_write_slot.ready = (
                            anyio.Event()
                        )  # one-shot reset so reconnect waiters pick up the next connection
                        _BRIDGE_RECONNECT.add(1, attributes={"reason": type(exc).__name__})
                        await supervisor_obj.fail_or_mark_for_resume(f"remote leader session reset: {exc!r}")
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
                        if not await _leader_recoverable():
                            _mark_leader_health_failed()
                            local_tg.cancel_scope.cancel()
                            return
                        await anyio.sleep(reconnect_delay(attempt, max_delay=BRIDGE_RECONNECT_MAX_SECONDS))
                        attempt += 1

            local_tg.start_soon(_local_forwarder)
            local_tg.start_soon(_remote_supervisor)
            local_tg.start_soon(supervisor_obj.watch_deadlines, 0.1, remote_reset_slot)
            if health_url is not None:
                # Raise the background watchdog's tolerance to cover the inline
                # reconnect recovery window, so it never tears down a follower the
                # inline loop is still reconnecting through a leader restart.
                monitor_max_failures = _monitor_max_failures_for_window(
                    heartbeat_max_failures, heartbeat_interval, BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS
                )
                local_tg.start_soon(
                    monitor_leader_health,
                    local_tg.cancel_scope,
                    health_url,
                    heartbeat_interval,
                    monitor_max_failures,
                    _mark_leader_health_failed,
                )
        if leader_health_failed:
            raise RuntimeError("leader health check failed")
