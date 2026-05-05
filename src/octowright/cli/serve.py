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
from typing import Any

import click
from provide.telemetry import get_logger, setup_telemetry, shutdown_telemetry

from ..server import mcp
from ._root import cli

_log = get_logger(__name__)


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
def serve(
    http_port: int | None,
    http_host: str | None,
    no_http: bool,
    keep_alive: bool,
    idle_grace: float | None,
    no_singleton: bool,
    daemon_mode: bool,
    log_level: str | None,
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
    of it. This way the leader is never a child of Claude Code (or any
    other MCP launcher), so SIGKILL on the launcher's child can't reach it.
    """
    from .. import daemonize as _daemon
    from .. import singleton as _sn

    # The daemon itself runs leader code directly — it knows it's the leader,
    # and it has no parent to follow.
    if daemon_mode:
        await _run_leader(
            http_host=http_host,
            http_port=http_port,
            no_http=no_http,
            keep_alive=keep_alive,
            idle_grace=idle_grace,
            no_singleton=False,
            arm_watchdog_immediately=True,
        )
        return

    # --no-singleton: legacy inline-leader mode (no daemon, no follower).
    if no_singleton:
        await _run_leader(
            http_host=http_host,
            http_port=http_port,
            no_http=no_http,
            keep_alive=keep_alive,
            idle_grace=idle_grace,
            no_singleton=True,
        )
        return

    existing = _sn.read_lock()
    leader_alive = existing is not None and not _sn.is_stale(existing) and await _sn.probe_http_alive(existing)

    # No healthy leader → spawn a daemonized one and follow it.
    if not leader_alive:
        click.echo("octowright: no live leader; spawning daemon", err=True)
        _daemon.spawn_daemon(http_host=http_host, http_port=http_port, idle_grace=idle_grace)
        existing = await _daemon.wait_for_daemon()
        if existing is None:
            # Daemon didn't come up in time — fall back to running leader inline
            # so the user at least gets a working server (browsers will die on
            # this process's exit, but that's better than no service at all).
            click.echo("octowright: daemon spawn timed out; running leader inline", err=True)
            await _run_leader(
                http_host=http_host,
                http_port=http_port,
                no_http=no_http,
                keep_alive=keep_alive,
                idle_grace=idle_grace,
                no_singleton=False,
            )
            return

    assert existing is not None
    try:
        await _run_follower(existing.mcp_url)
    except Exception as exc:
        click.echo(f"octowright: leader bridge ended ({exc}); checking daemon", err=True)
    else:
        click.echo("octowright: leader bridge closed; checking daemon", err=True)

    # Re-check: did the daemon really go away? If yes, spawn a fresh one and
    # exit — we don't run leader inline here either (we'd just die with the
    # parent's next signal). One spawn attempt is enough.
    recheck = _sn.read_lock()
    still_alive = recheck is not None and not _sn.is_stale(recheck) and await _sn.probe_http_alive(recheck)
    if still_alive:
        click.echo("octowright: leader still healthy, exiting", err=True)
        return
    click.echo("octowright: leader is gone; spawning replacement daemon", err=True)
    _daemon.spawn_daemon(http_host=http_host, http_port=http_port, idle_grace=idle_grace)


async def _run_follower(leader_mcp_url: str) -> None:
    """Bridge stdio to the leader's HTTP-MCP endpoint."""
    from ..proxy_bridge import run_proxy

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

    from .. import http as _http
    from .. import singleton as _sn
    from ..defaults import (
        HTTP_HOST,
        HTTP_PORT,
        HTTP_PORT_RETRIES,
        IDLE_GRACE_SECONDS,
        IDLE_POLL_SECONDS,
    )
    from ..idle_watchdog import idle_watchdog
    from ..server._state import pool, scenario_pool

    grace = idle_grace if idle_grace is not None else IDLE_GRACE_SECONDS
    bound_host = http_host or HTTP_HOST
    bound_port = http_port if http_port is not None else HTTP_PORT

    def _on_http_bound(host: str, port: int) -> None:
        if no_singleton:
            return
        info = _sn.make_leader_info(host, port)
        _sn.write_lock(info)

    mcp_task = _asyncio.create_task(mcp.run_stdio_async(), name="octowright.mcp")
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
    # Followers can find and connect to us, so a stdio EOF (e.g. Claude Code
    # closes) doesn't mean we're useless — keep serving until the watchdog
    # fires or a sidecar fails.
    discoverable = not no_http and not no_singleton

    # Convert SIGTERM/SIGHUP (sent by parent MCP clients on close) into a
    # graceful "stdio done" signal — cancel mcp_task and let the keep-alive
    # path below take over. SIGINT keeps default behavior so Ctrl+C still
    # exits the process for interactive users. Only install on a discoverable
    # leader; in --no-http / --no-singleton modes the leader is single-purpose
    # and the default "exit on signal" semantics are correct.
    import signal as _signal

    loop = _asyncio.get_running_loop()
    installed_signals: list[_signal.Signals] = []
    if discoverable:
        for sig in (_signal.SIGTERM, _signal.SIGHUP):
            try:
                loop.add_signal_handler(sig, mcp_task.cancel)
                installed_signals.append(sig)
            except (NotImplementedError, ValueError):
                # Windows / nested loops can't install handlers — fall back to
                # default behavior, which means SIGTERM still kills us there.
                pass

    wait_for: set[_asyncio.Task[object]] = {mcp_task}
    if watch_task is not None:
        wait_for.add(watch_task)

    try:
        await _asyncio.wait(wait_for, return_when=_asyncio.FIRST_COMPLETED)
        _log_first_done("octowright.leader.first_phase_ended", mcp_task, watch_task, sidecars)

        # If only the stdio MCP task ended (the typical "client disconnected"
        # case) and we're discoverable, keep serving via HTTP-MCP. The
        # watchdog or a sidecar failure will eventually end us; the user
        # reopening their MCP client will spawn a new follower that bridges
        # back here without losing browser state.
        if mcp_task.done() and discoverable and watch_task is not None and not watch_task.done():
            click.echo(
                "octowright: stdio client disconnected; leader staying alive for HTTP-MCP "
                "(reconnect by reopening your MCP client; auto-quit governed by --idle-grace)",
                err=True,
            )
            await _asyncio.wait(
                {watch_task, *sidecars},
                return_when=_asyncio.FIRST_COMPLETED,
            )
            _log_first_done("octowright.leader.second_phase_ended", mcp_task, watch_task, sidecars)
    finally:
        for sig in installed_signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, ValueError):
                pass
        for t in (*sidecars, watch_task, mcp_task):
            if t is not None and not t.done():
                t.cancel()
        for t in (*sidecars, watch_task, mcp_task):
            if t is None:
                continue
            try:
                await t
            except (_asyncio.CancelledError, Exception):
                pass
        if not no_singleton:
            _sn.remove_lock()
