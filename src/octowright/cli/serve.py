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
def serve(http_port: int | None, http_host: str | None, no_http: bool) -> None:
    """Run the MCP stdio server plus the HTTP debugger sidecar (default).

    The HTTP debugger lives at http://127.0.0.1:8765/ by default and exposes a
    web dashboard for live and historical browser sessions. Use `--no-http`
    to fall back to MCP-only mode.
    """
    import asyncio as _asyncio

    from .. import http as _http
    from ..defaults import HTTP_HOST, HTTP_PORT, HTTP_PORT_RETRIES

    setup_telemetry()

    async def _run_both() -> None:
        # The MCP stdio server is the foreground worker; the HTTP server is a
        # sidecar. When MCP exits (stdin EOF), cancel the HTTP task so the
        # process can shut down cleanly.
        mcp_task = _asyncio.create_task(mcp.run_stdio_async(), name="octowright.mcp")
        if no_http:
            await mcp_task
            return

        http_task = _asyncio.create_task(
            _http.serve_app(
                host=http_host or HTTP_HOST,
                port=http_port if http_port is not None else HTTP_PORT,
                retries=HTTP_PORT_RETRIES,
            ),
            name="octowright.http",
        )
        try:
            await mcp_task
        finally:
            if not http_task.done():
                http_task.cancel()
                try:
                    await http_task
                except _asyncio.CancelledError:
                    pass

    try:
        _asyncio.run(_run_both())
    finally:
        shutdown_telemetry()
