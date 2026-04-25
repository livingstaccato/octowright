# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright serve`` — run MCP stdio + the HTTP debugger sidecar.

The first instance becomes the **leader**: it serves stdio MCP, the HTTP
debugger, and an HTTP-MCP transport at ``/mcp`` so subsequent instances can
proxy through it. It records itself in ``~/.config/undef/octowright.lock``.

Subsequent instances become **followers**: they detect the live leader and
bridge stdin/stdout to its HTTP-MCP endpoint instead of spawning a pool. Pass
``--no-singleton`` to bypass the lock (useful for tests and debugging).
"""

from __future__ import annotations

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from ..server import mcp
from ._root import cli


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
def serve(
    http_port: int | None,
    http_host: str | None,
    no_http: bool,
    keep_alive: bool,
    idle_grace: float | None,
    no_singleton: bool,
) -> None:
    """Run the MCP stdio server plus the HTTP debugger sidecar (default).

    The HTTP debugger lives at http://127.0.0.1:8765/ by default. By default,
    once at least one browser or scenario has existed, the server exits after
    the pool has been empty for ``--idle-grace`` seconds. Pass ``--keep-alive``
    to disable this. Pass ``--no-singleton`` to bypass the leader/follower
    coordination — useful for tests, but means no shared pool with peers.
    """
    import asyncio as _asyncio

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
) -> None:
    """Decide leader vs follower; promote follower→leader on bridge failure.

    Capped at one promotion attempt per process so a stuck loop cannot turn
    into a runaway respawn cycle.
    """
    from .. import singleton as _sn

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

    if leader_alive:
        assert existing is not None
        try:
            await _run_follower(existing.mcp_url)
        except Exception as exc:
            click.echo(f"octowright: leader bridge ended ({exc}); attempting promotion", err=True)
        else:
            # Clean exit (leader closed our stream cleanly) — also promote if
            # the leader is now actually gone.
            click.echo("octowright: leader bridge closed; checking for promotion", err=True)

        # Re-check: did the leader actually go away?
        recheck = _sn.read_lock()
        still_alive = recheck is not None and not _sn.is_stale(recheck) and await _sn.probe_http_alive(recheck)
        if still_alive:
            click.echo("octowright: leader still healthy, exiting", err=True)
            return
        click.echo("octowright: promoting self to leader", err=True)

    await _run_leader(
        http_host=http_host,
        http_port=http_port,
        no_http=no_http,
        keep_alive=keep_alive,
        idle_grace=idle_grace,
        no_singleton=False,
    )


async def _run_follower(leader_mcp_url: str) -> None:
    """Bridge stdio to the leader's HTTP-MCP endpoint."""
    from ..proxy_bridge import run_proxy

    click.echo(f"octowright: connecting to leader at {leader_mcp_url}", err=True)
    await run_proxy(leader_mcp_url)


async def _run_leader(
    *,
    http_host: str | None,
    http_port: int | None,
    no_http: bool,
    keep_alive: bool,
    idle_grace: float | None,
    no_singleton: bool,
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
            ),
            name="octowright.idle_watchdog",
        )

    wait_for: set[_asyncio.Task[object]] = {mcp_task}
    if watch_task is not None:
        wait_for.add(watch_task)

    try:
        await _asyncio.wait(wait_for, return_when=_asyncio.FIRST_COMPLETED)
    finally:
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
