# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright serve`` — run MCP stdio + the HTTP debugger sidecar."""

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
def serve(
    http_port: int | None,
    http_host: str | None,
    no_http: bool,
    keep_alive: bool,
    idle_grace: float | None,
) -> None:
    """Run the MCP stdio server plus the HTTP debugger sidecar (default).

    The HTTP debugger lives at http://127.0.0.1:8765/ by default and exposes a
    web dashboard for live and historical browser sessions. Use `--no-http`
    to fall back to MCP-only mode.

    By default, once at least one browser or scenario has existed, the server
    exits after the pool has been empty for ``--idle-grace`` seconds. Pass
    ``--keep-alive`` to disable this.
    """
    import asyncio as _asyncio

    from .. import http as _http
    from ..defaults import (
        HTTP_HOST,
        HTTP_PORT,
        HTTP_PORT_RETRIES,
        IDLE_GRACE_SECONDS,
        IDLE_POLL_SECONDS,
    )
    from ..idle_watchdog import idle_watchdog
    from ..server._state import pool, scenario_pool

    setup_telemetry()

    grace = idle_grace if idle_grace is not None else IDLE_GRACE_SECONDS

    async def _run_all() -> None:
        # The MCP stdio server is the foreground worker; everything else is a
        # sidecar. We exit when EITHER (a) MCP exits (stdin EOF) or (b) the
        # idle watchdog fires.
        mcp_task = _asyncio.create_task(mcp.run_stdio_async(), name="octowright.mcp")
        sidecars: list[_asyncio.Task[object]] = []

        if not no_http:
            sidecars.append(
                _asyncio.create_task(
                    _http.serve_app(
                        host=http_host or HTTP_HOST,
                        port=http_port if http_port is not None else HTTP_PORT,
                        retries=HTTP_PORT_RETRIES,
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

    try:
        _asyncio.run(_run_all())
    finally:
        shutdown_telemetry()
