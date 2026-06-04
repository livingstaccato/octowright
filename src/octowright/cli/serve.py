# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright serve`` — run MCP stdio + the HTTP debugger sidecar.

The first instance becomes the **leader**: it serves stdio MCP, the HTTP
debugger, and an HTTP-MCP transport at ``/mcp`` so subsequent instances can
proxy through it. It records itself in Octowright's user config directory.
Subsequent instances become **followers**: they detect the live leader and
bridge stdin/stdout to its HTTP-MCP endpoint instead of spawning a pool. Pass
``--no-singleton`` to bypass the lock (useful for tests and debugging).
"""

from __future__ import annotations

import asyncio as _asyncio_mod
from collections.abc import Callable
from types import FrameType
from typing import Any

import click
from provide.telemetry import get_logger, setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli

_log = get_logger(__name__)

_SignalHandler = Callable[[int, FrameType | None], Any] | int | None


def _log_first_done(
    event: str,
    mcp_task: _asyncio_mod.Task[Any],
    watch_task: _asyncio_mod.Task[Any] | None,
    sidecars: list[_asyncio_mod.Task[Any]],
) -> None:
    """Log which task ended first so a daemon shutdown is attributable.

    Logged at INFO so it shows up in the default daemon log without needing
    --log-level=DEBUG. Includes the task that ended first plus a snapshot of
    the others' done/cancelled state so the user can tell whether shutdown
    came from the idle watchdog, a crashed sidecar, or stdio EOF.
    """
    finished: list[str] = []
    pending: list[str] = []
    for label, task in [("mcp", mcp_task), ("watchdog", watch_task)] + [
        (f"sidecar[{i}]", t) for i, t in enumerate(sidecars)
    ]:
        if task is None:
            continue
        if task.done():
            exc = task.exception() if not task.cancelled() else None
            tag = "cancelled" if task.cancelled() else ("error" if exc else "ok")
            finished.append(f"{label}={tag}")
        else:
            pending.append(label)
    _log.info(event, finished=finished, pending=pending)


@cli.command()
@click.option(
    "--http-port",
    "http_port",
    type=int,
    default=None,
    help="HTTP debugger port (overrides OCTOWRIGHT_HTTP_PORT, default 8765).",
)
@click.option(
    "--http-host",
    "http_host",
    default=None,
    help="HTTP debugger bind host (overrides OCTOWRIGHT_HTTP_HOST, default 127.0.0.1).",
)
@click.option(
    "--no-http",
    "no_http",
    is_flag=True,
    help="Disable the HTTP debugger sidecar (MCP-only mode).",
)
@click.option(
    "--keep-alive",
    "keep_alive",
    is_flag=True,
    help="Disable the idle-watchdog auto-quit; serve until killed or stdin EOF.",
)
@click.option(
    "--idle-grace",
    "idle_grace",
    type=float,
    default=None,
    help="Seconds the pool must sit empty before auto-quit (overrides OCTOWRIGHT_IDLE_GRACE).",
)
@click.option(
    "--no-singleton",
    "no_singleton",
    is_flag=True,
    help="Bypass leader-election and lockfile; always serve standalone.",
)
@click.option(
    "--daemon-mode",
    "daemon_mode",
    is_flag=True,
    hidden=True,
    help="Internal: this process IS the daemonized leader. Skip lock check, "
    "arm watchdog immediately. Set by spawn_daemon(); never invoke directly.",
)
@click.option(
    "--log-level",
    "log_level",
    type=click.Choice(["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False),
    default=None,
    help="Set PROVIDE_LOG_LEVEL for this process and any spawned daemon. "
    "Use DEBUG when investigating watchdog/shutdown behavior; daemon output "
    "lands in Octowright's user config directory.",
)
@click.option(
    "--profile",
    "profile",
    default=None,
    help="Restrict the MCP tool surface to the named capability profile(s) "
    "(comma-separated, e.g. 'core' or 'core,advanced'). Sets OCTOWRIGHT_PROFILE "
    "for this process and any spawned daemon. Use 'all' or omit for the full "
    "tool surface (default). See octowright.server.profiles.PROFILES.",
)
def serve(
    http_port: int | None,
    http_host: str | None,
    no_http: bool,
    keep_alive: bool,
    idle_grace: float | None,
    no_singleton: bool,
    daemon_mode: bool,
    log_level: str | None,
    profile: str | None,
) -> None:
    """Run the MCP stdio server plus the HTTP debugger sidecar (default).

    The HTTP debugger lives at http://127.0.0.1:8765/ by default. By default,
    once at least one browser or scenario has existed, the server exits after
    the pool has been empty for ``--idle-grace`` seconds. Pass ``--keep-alive``
    to disable this. Pass ``--no-singleton`` to bypass the leader/follower
    coordination — useful for tests, but means no shared pool with peers.
    """
    import asyncio as _asyncio
    import os as _os

    # Set the env var BEFORE setup_telemetry so the logger picks it up.
    # Also export it so spawned daemons inherit it (daemonize uses
    # os.environ.copy()).
    if log_level is not None:
        _os.environ["PROVIDE_LOG_LEVEL"] = log_level.upper()

    # Same export rationale: must land before any tool module imports so
    # the @mcp.tool filter sees it, and so the daemon child inherits it.
    if profile is not None:
        _os.environ["OCTOWRIGHT_PROFILE"] = profile

    setup_telemetry()
    try:
        _asyncio.run(
            _serve_async(
                http_host=http_host,
                http_port=http_port,
                no_http=no_http,
                keep_alive=keep_alive,
                idle_grace=idle_grace,
                no_singleton=no_singleton,
                daemon_mode=daemon_mode,
            )
        )
    finally:
        shutdown_telemetry()


async def _serve_async(
    *,
    http_host: str | None,
    http_port: int | None,
    no_http: bool,
    keep_alive: bool,
    idle_grace: float | None,
    no_singleton: bool,
    daemon_mode: bool = False,
) -> None:
    """Decide leader vs follower; promote follower→leader on bridge failure.

    With singleton coordination on (the default), a fresh invocation that
    finds no live leader spawns a **detached daemon** and becomes a follower
    of it. This way the leader is never a child of the MCP launcher, so
    SIGKILL on the launcher's child can't reach it.
    """
    leader_kwargs: dict[str, Any] = {
        "http_host": http_host,
        "http_port": http_port,
        "no_http": no_http,
        "keep_alive": keep_alive,
        "idle_grace": idle_grace,
    }
    # Direct-leader paths: daemon-mode (the spawned daemon runs leader code
    # directly) and --no-singleton (legacy inline mode, no daemon, no follower).
    if daemon_mode:
        await _run_leader(**leader_kwargs, no_singleton=False, arm_watchdog_immediately=True)
        return
    if no_singleton:
        await _run_leader(**leader_kwargs, no_singleton=True)
        return
    await _serve_singleton(leader_kwargs, http_host=http_host, http_port=http_port, idle_grace=idle_grace)


async def _ensure_leader_or_inline(
    leader_kwargs: dict[str, Any],
    *,
    http_host: str | None,
    http_port: int | None,
    idle_grace: float | None,
) -> Any:
    """Find or spawn a daemon leader. Returns leader info, OR None when
    we fell back to running the leader inline (caller returns immediately)."""
    from octowright import daemonize as _daemon
    from octowright import singleton as _sn

    # Probe outside the lock; recheck under it to avoid duplicate spawn.
    if (found := await _probe_alive_leader(_sn)) is not None:
        return found
    async with _sn.async_election_lock():
        if (found := await _probe_alive_leader(_sn)) is not None:
            return found
        click.echo("octowright: no live leader; spawning daemon", err=True)
        _daemon.spawn_daemon(http_host=http_host, http_port=http_port, idle_grace=idle_grace)
    spawned = await _daemon.wait_for_daemon()
    if spawned is None:
        # Daemon didn't come up — run leader inline so the user at least gets
        # a working server (browsers die on this process's exit).
        click.echo("octowright: daemon spawn timed out; running leader inline", err=True)
        await _run_leader(**leader_kwargs, no_singleton=False)
        return None
    return spawned


async def _bridge_to_leader(leader_info: Any) -> None:
    """Run the follower bridge; log how it ended (exception vs clean close)."""
    try:
        await _run_follower(leader_info.mcp_url)
    except Exception as exc:
        click.echo(f"octowright: leader bridge ended ({exc}); checking daemon", err=True)
    else:
        click.echo("octowright: leader bridge closed; checking daemon", err=True)


async def _probe_alive_leader(sn: Any) -> Any | None:
    """Return LeaderInfo iff lockfile + HTTP probe both confirm live."""
    info = sn.read_lock()
    return info if info and not sn.is_stale(info) and await sn.probe_http_alive(info) else None


async def _respawn_if_leader_gone(*, http_host: str | None, http_port: int | None, idle_grace: float | None) -> None:
    from octowright import daemonize as _daemon
    from octowright import singleton as _sn

    if await _probe_alive_leader(_sn) is not None:
        click.echo("octowright: leader still healthy, exiting", err=True)
        return
    async with _sn.async_election_lock():
        if await _probe_alive_leader(_sn) is not None:
            click.echo("octowright: leader still healthy, exiting", err=True)
            return
        click.echo("octowright: leader is gone; spawning replacement daemon", err=True)
        _daemon.spawn_daemon(http_host=http_host, http_port=http_port, idle_grace=idle_grace)
    if await _daemon.wait_for_daemon() is None:
        click.echo("octowright: replacement daemon spawn timed out", err=True)


async def _serve_singleton(
    leader_kwargs: dict[str, Any],
    *,
    http_host: str | None,
    http_port: int | None,
    idle_grace: float | None,
) -> None:
    """The default singleton-coordinated path: find or spawn a daemon leader,
    follow it, and re-spawn if it dies mid-session."""
    existing = await _ensure_leader_or_inline(
        leader_kwargs, http_host=http_host, http_port=http_port, idle_grace=idle_grace
    )
    if existing is None:
        return
    await _bridge_to_leader(existing)
    await _respawn_if_leader_gone(http_host=http_host, http_port=http_port, idle_grace=idle_grace)


async def _run_follower(leader_mcp_url: str) -> None:
    """Bridge stdio to the leader's HTTP-MCP endpoint."""
    from octowright.proxy_bridge import run_proxy

    # Same host:port serves /api/health — used by the bridge watchdog to
    # detect a wedged leader (silent SSE) and tear down rather than hang.
    health_url = leader_mcp_url.rsplit("/mcp", 1)[0] + "/api/health"
    click.echo(f"octowright: connecting to leader at {leader_mcp_url}", err=True)
    await run_proxy(leader_mcp_url, health_url=health_url)


async def _run_leader(
    *,
    http_host: str | None,
    http_port: int | None,
    no_http: bool,
    keep_alive: bool,
    idle_grace: float | None,
    no_singleton: bool,
    arm_watchdog_immediately: bool = False,
) -> None:
    """Serve MCP stdio + HTTP debugger + (when not --no-http) HTTP-MCP."""
    import asyncio as _asyncio

    from octowright import http as _http
    from octowright import singleton as _sn
    from octowright.defaults import (
        HTTP_HOST,
        HTTP_PORT,
        HTTP_PORT_RETRIES,
        IDLE_GRACE_SECONDS,
        IDLE_POLL_SECONDS,
    )
    from octowright.idle_watchdog import idle_watchdog
    from octowright.server import mcp
    from octowright.server._state import pool, scenario_pool
    from octowright.server.mcp_notifications import run_stdio_with_notifications

    grace = idle_grace if idle_grace is not None else IDLE_GRACE_SECONDS
    bound_host = http_host or HTTP_HOST
    bound_port = http_port if http_port is not None else HTTP_PORT

    def _on_http_bound(host: str, port: int) -> None:
        from octowright.defaults import set_actual_http_port

        set_actual_http_port(port)
        if no_singleton:
            return
        info = _sn.make_leader_info(host, port)
        _sn.write_lock(info)

    mcp_task = _asyncio.create_task(run_stdio_with_notifications(mcp), name="octowright.mcp")
    sidecars: list[_asyncio.Task[object]] = []

    if not no_http:
        sidecars.append(
            _asyncio.create_task(
                _http.serve_app(
                    host=bound_host,
                    port=bound_port,
                    retries=HTTP_PORT_RETRIES,
                    mcp_leader=not no_singleton,
                    on_bound=_on_http_bound,
                ),
                name="octowright.http",
            )
        )

    watch_task: _asyncio.Task[None] | None = None
    if not keep_alive:
        watch_task = _asyncio.create_task(
            idle_watchdog(
                pool,
                scenario_pool,
                grace_seconds=grace,
                poll_seconds=IDLE_POLL_SECONDS,
                arm_immediately=arm_watchdog_immediately,
                get_extra_active_count=_http.get_mcp_active_session_count if not no_http else None,
            ),
            name="octowright.idle_watchdog",
        )

    # Discoverable leader: HTTP-MCP at /mcp/ is up AND we wrote the lockfile.
    # Followers can find and connect to us, so a stdio EOF (e.g. MCP client
    # closes) doesn't mean we're useless — keep serving until the watchdog
    # fires or a sidecar fails.
    discoverable = not no_http and not no_singleton

    # Signal handlers: see _install_leader_signal_handlers for rationale.
    loop = _asyncio.get_running_loop()
    installed_signals, installed_signal_handlers = _install_leader_signal_handlers(loop, mcp_task, discoverable)

    wait_for: set[_asyncio.Task[object]] = {mcp_task}
    if watch_task is not None:
        wait_for.add(watch_task)

    try:
        await _run_leader_phases(wait_for, mcp_task, watch_task, sidecars, discoverable)
    finally:
        _uninstall_leader_signal_handlers(loop, installed_signals, installed_signal_handlers)
        await _cancel_and_collect_tasks(sidecars, watch_task, mcp_task)
        from octowright.process_reaper import reap_descendant_browsers_on_shutdown

        await reap_descendant_browsers_on_shutdown(pool, log=_log)
        if not no_singleton:
            _sn.remove_lock()


def _install_leader_signal_handlers(
    loop: Any, mcp_task: Any, discoverable: bool
) -> tuple[list[Any], list[tuple[Any, _SignalHandler]]]:
    """Convert SIGTERM/SIGHUP (sent by parent MCP clients on close) into a
    graceful "stdio done" signal — cancel mcp_task and let the leader's
    keep-alive path take over. SIGINT keeps default behavior so Ctrl+C still
    exits the process for interactive users. Only install on a discoverable
    leader; in --no-http / --no-singleton modes the default "exit on signal"
    semantics are correct. Register/fallback failures are logged."""
    import signal as _signal

    installed_signals: list[Any] = []
    installed_signal_handlers: list[tuple[Any, _SignalHandler]] = []
    if not discoverable:
        return installed_signals, installed_signal_handlers
    signals = [_signal.SIGTERM]
    if hasattr(_signal, "SIGHUP"):
        signals.append(_signal.SIGHUP)
    for sig in signals:
        name = sig.name if hasattr(sig, "name") else int(sig)
        try:
            loop.add_signal_handler(sig, mcp_task.cancel)
            installed_signals.append(sig)
        except (NotImplementedError, ValueError) as loop_exc:
            _log.warning("octowright.serve.signal_handler_register_failed", signal=name, error=repr(loop_exc))
            try:
                previous = _signal.getsignal(sig)
                _signal.signal(sig, lambda *_args: loop.call_soon_threadsafe(mcp_task.cancel))
                installed_signal_handlers.append((sig, previous))
            except (OSError, RuntimeError, ValueError) as exc:
                _log.warning("octowright.serve.signal_handler_fallback_failed", signal=name, error=repr(exc))
    return installed_signals, installed_signal_handlers


def _uninstall_leader_signal_handlers(
    loop: Any,
    installed_signals: list[Any],
    installed_signal_handlers: list[tuple[Any, _SignalHandler]],
) -> None:
    """Restore signal handlers — best-effort; we're already shutting down."""
    import signal as _signal

    for sig in installed_signals:
        try:
            loop.remove_signal_handler(sig)
        except (NotImplementedError, ValueError):
            pass
    for sig, previous in installed_signal_handlers:
        try:
            _signal.signal(sig, previous)
        except (OSError, RuntimeError, ValueError):
            pass


async def _cancel_and_collect_tasks(
    sidecars: list[Any],
    watch_task: Any,
    mcp_task: Any,
) -> None:
    import asyncio as _asyncio

    for t in (*sidecars, watch_task, mcp_task):
        if t is not None and not t.done():
            t.cancel()
    for t in (*sidecars, watch_task, mcp_task):
        if t is None:
            continue
        try:
            await t
        except _asyncio.CancelledError:
            pass
        except Exception as exc:
            _log.debug("serve.task_cancel.exception", error=repr(exc))


async def _run_leader_phases(
    wait_for: set[Any],
    mcp_task: Any,
    watch_task: Any,
    sidecars: list[Any],
    discoverable: bool,
) -> None:
    """Two-phase leader life: wait for stdio-or-watchdog, then if only the
    stdio task ended on a discoverable leader, keep serving via HTTP-MCP
    until the watchdog or a sidecar fires."""
    import asyncio as _asyncio

    await _asyncio.wait(wait_for, return_when=_asyncio.FIRST_COMPLETED)
    _log_first_done("octowright.leader.first_phase_ended", mcp_task, watch_task, sidecars)

    # If only the stdio MCP task ended (the typical "client disconnected"
    # case) and we're discoverable, keep serving via HTTP-MCP. The watchdog
    # or a sidecar failure will eventually end us; the user reopening their
    # MCP client will spawn a new follower that bridges back here without
    # losing browser state.
    if mcp_task.done() and discoverable and watch_task is not None and not watch_task.done():
        click.echo(
            "octowright: stdio client disconnected; leader staying alive for HTTP-MCP "
            "(reconnect by reopening your MCP client; auto-quit governed by --idle-grace)",
            err=True,
        )
        await _asyncio.wait({watch_task, *sidecars}, return_when=_asyncio.FIRST_COMPLETED)
        _log_first_done("octowright.leader.second_phase_ended", mcp_task, watch_task, sidecars)
