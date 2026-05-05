# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Bind + serve coordination for the HTTP debugger sidecar.

Runs uvicorn in the *current* event loop so it shares scheduling with the MCP
stdio task started by ``cli serve``. If the preferred port is busy, walks up
to ``retries`` additional ports before giving up — on total failure the MCP
server keeps running and the dashboard tool reports the bind error.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

from ..defaults import HTTP_HOST, HTTP_PORT, HTTP_PORT_RETRIES
from . import state
from .app import build_app


def _port_is_free(host: str, port: int) -> bool:
    try:
        addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    if not addrinfos:
        return False

    for family, socktype, proto, _canonname, sockaddr in addrinfos:
        try:
            s = socket.socket(family, socktype, proto)
        except OSError:
            continue
        try:
            s.bind(sockaddr)
            return True
        except OSError:
            continue
        finally:
            s.close()
    return False


def _pick_port(host: str, preferred: int, retries: int) -> int | None:
    """Try preferred port; fall back to next ``retries`` ports. Returns None on failure."""
    for offset in range(retries + 1):
        candidate = preferred + offset
        if _port_is_free(host, candidate):
            return candidate
    return None


async def serve_app(
    *,
    host: str = HTTP_HOST,
    port: int = HTTP_PORT,
    retries: int = HTTP_PORT_RETRIES,
    mcp_leader: bool = False,
    on_bound: Callable[[str, int], None] | None = None,
) -> None:
    """Run uvicorn in the current event loop until cancelled.

    Designed for `asyncio.gather(mcp_task, http_task)` in `cli.py serve`. If
    the preferred port is busy, walks up to ``retries`` ports before giving
    up. On total failure, logs and returns — the MCP server keeps running.

    When ``mcp_leader`` is True, the app also exposes FastMCP's streamable-HTTP
    transport at ``/mcp`` so follower octowright instances can bridge to it.
    """
    bound = _pick_port(host, port, retries)
    if bound is None:
        state._RUNTIME_ERROR = f"port {port} (and {retries} fallbacks) all in use; HTTP debugger disabled"
        state.log.warning("octowright.http.bind_failed", host=host, preferred=port, retries=retries)
        return

    import uvicorn

    app = build_app(mcp_leader=mcp_leader)
    app.state.octowright_http_host = host

    config = uvicorn.Config(
        app=app,
        host=host,
        port=bound,
        log_level="warning",
        access_log=False,
        # Reuse the running loop — this is the whole point of the sidecar.
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    state._RUNTIME_HOST = host
    state._RUNTIME_PORT = bound
    state._RUNTIME_ERROR = None
    state.log.info("octowright.http.listening", host=host, port=bound)
    if on_bound is not None:
        on_bound(host, bound)
    try:
        await server.serve()
    finally:
        state._RUNTIME_HOST = None
        state._RUNTIME_PORT = None
        state.log.info("octowright.http.stopped", host=host, port=bound)
