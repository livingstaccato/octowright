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

from octowright.defaults import HTTP_HOST, HTTP_PORT, HTTP_PORT_RETRIES
from octowright.http import state
from octowright.http.app import build_app


def _port_is_free(host: str, port: int) -> bool:
    try:
        addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    if not addrinfos:
        return False

    seen: set[tuple[int, int, int, object]] = set()
    checked = False
    for family, socktype, proto, _canonname, sockaddr in addrinfos:
        key = (family, socktype, proto, sockaddr)
        if key in seen:
            continue
        seen.add(key)
        try:
            s = socket.socket(family, socktype, proto)
        except OSError:
            continue
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(sockaddr)
            checked = True
        except OSError:
            return False
        finally:
            s.close()
    return checked


def _pick_port(host: str, preferred: int, retries: int) -> int | None:
    """Try preferred port; fall back to next ``retries`` ports. Returns None on failure."""
    for offset in range(retries + 1):
        candidate = preferred + offset
        if _port_is_free(host, candidate):
            return candidate
    return None


def _bind_server_socket(host: str, port: int) -> socket.socket:
    """Create a pre-bound server socket with SO_REUSEADDR + SO_REUSEPORT.

    Holding the socket while uvicorn starts means no other process can steal
    the port in the gap between _port_is_free() and uvicorn's own bind.
    SO_REUSEPORT (where available) lets a restarted daemon bind to a port
    whose old socket is still in TIME_WAIT.
    """
    addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    family, socktype, proto, _, sockaddr = addrinfos[0]
    s = socket.socket(family, socktype, proto)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(sockaddr)
    s.set_inheritable(True)
    return s


async def serve_app(
    *,
    host: str = HTTP_HOST,
    port: int = HTTP_PORT,
    retries: int = HTTP_PORT_RETRIES,
    mcp_leader: bool = False,
    mcp_token: str = "",
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

    app = build_app(mcp_leader=mcp_leader, host=host, mcp_token=mcp_token)

    # Pre-bind with SO_REUSEADDR + SO_REUSEPORT so a restarted daemon claims
    # the port immediately, and no other process can steal it between the
    # _port_is_free probe and uvicorn's own bind.
    srv_socket = _bind_server_socket(host, bound)

    # When sockets= is given, uvicorn skips its own bind; host/port in Config
    # become metadata only (used for display/logging).
    config = uvicorn.Config(
        app=app,
        host=host,
        port=bound,
        log_level="warning",
        access_log=False,
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
        await server.serve(sockets=[srv_socket])
    finally:
        if srv_socket is not None:
            srv_socket.close()
        state._RUNTIME_HOST = None
        state._RUNTIME_PORT = None
        state.log.info("octowright.http.stopped", host=host, port=bound)
