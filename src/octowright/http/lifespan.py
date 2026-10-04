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

from octowright._port_probe import port_is_free, set_listen_options
from octowright.defaults import HTTP_HOST, HTTP_PORT, HTTP_PORT_RETRIES
from octowright.http import state
from octowright.http.app import build_app

_port_is_free = port_is_free


# Bound on uvicorn's graceful shutdown. Its default waits with no limit for
# every open connection to finish, and every connected follower holds a
# never-ending SSE stream (/api/mcp-events, the /mcp GET stream) -- so a leader
# with any client attached never left serve() on SIGTERM, the sender escalated
# to SIGKILL, and pool/plugin teardown and the leader's lock removal were
# skipped. Ordinary requests finish well inside this; past it the remaining
# connection tasks are cancelled and the followers reconnect to the next leader.
HTTP_GRACEFUL_SHUTDOWN_SECONDS = 2  # uvicorn types this as int


def _bind_server_socket(host: str, port: int) -> socket.socket:
    """Bind AND listen on ``host:port``: the claim itself, not a reservation.

    Listening here (uvicorn re-calls ``listen`` with its backlog, harmlessly)
    makes the claim exclusive the moment it succeeds, with the options in
    ``_port_probe``: a second leader's bind on this port now fails instead of
    joining it. The socket is held while uvicorn starts so nothing can take
    the port in between.
    """
    addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    family, socktype, proto, _, sockaddr = addrinfos[0]
    s = socket.socket(family, socktype, proto)
    try:
        set_listen_options(s)
        s.bind(sockaddr)
        s.listen()
    except BaseException:
        s.close()
        raise
    s.set_inheritable(True)
    return s


def _claim_port(host: str, preferred: int, retries: int) -> tuple[socket.socket, int] | None:
    """Claim the first of ``preferred`` .. ``preferred + retries`` this process
    can bind and listen on; None when none can be. The probe is only a filter
    (it covers every address the host resolves to); the bind decides, so two
    leaders racing for one port cannot both get it -- the loser walks on."""
    for offset in range(retries + 1):
        candidate = preferred + offset
        if not _port_is_free(host, candidate):
            continue
        try:
            return _bind_server_socket(host, candidate), candidate
        except OSError:
            continue
    return None


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

    When ``mcp_leader`` is True, the app also exposes the MCP server's streamable-HTTP
    transport at ``/mcp`` so follower octowright instances can bridge to it.
    """
    claimed = _claim_port(host, port, retries)
    if claimed is None:
        state._RUNTIME_ERROR = f"port {port} (and {retries} fallbacks) all in use; HTTP debugger disabled"
        state.log.warning("octowright.http.bind_failed", host=host, preferred=port, retries=retries)
        return
    srv_socket, bound = claimed
    try:
        import uvicorn

        app = build_app(mcp_leader=mcp_leader, host=host, mcp_token=mcp_token)
        # When sockets= is given, uvicorn skips its own bind; host/port in Config
        # become metadata only (used for display/logging).
        config = uvicorn.Config(
            app=app,
            host=host,
            port=bound,
            log_level="warning",
            access_log=False,
            loop="asyncio",
            timeout_graceful_shutdown=HTTP_GRACEFUL_SHUTDOWN_SECONDS,
        )
        server = uvicorn.Server(config)
        state._RUNTIME_HOST = host
        state._RUNTIME_PORT = bound
        state._RUNTIME_ERROR = None
        state.log.info("octowright.http.listening", host=host, port=bound)
        if on_bound is not None:
            on_bound(host, bound)
        await server.serve(sockets=[srv_socket])
    finally:
        srv_socket.close()
        state._RUNTIME_HOST = None
        state._RUNTIME_PORT = None
        state.log.info("octowright.http.stopped", host=host, port=bound)
