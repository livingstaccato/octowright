# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Starlette app factory.

Assembles the API route list (from ``routes/``) plus the SPA frontend mount.
The frontend goes last so its catchall StaticFiles mount at ``/`` doesn't
shadow API routes.

When ``mcp_leader=True``, also mounts the FastMCP streamable-HTTP transport at
``/mcp`` and delegates lifespan to it so the session manager starts/stops with
the server. Followers connect to that endpoint instead of spawning their own
browser pool — see ``octowright.singleton`` and ``cli.serve``.
"""

from __future__ import annotations

from typing import Any

from starlette.applications import Starlette
from starlette.routing import Mount

from .frontend import _frontend_routes
from .metrics import HttpMetricsMiddleware, metrics_enabled
from .routes import all_routes

# Set by build_app(mcp_leader=True); used by idle_watchdog to count active
# HTTP-MCP proxy sessions so the daemon doesn't exit while followers are live.
_mcp_session_manager: Any = None


def get_mcp_active_session_count() -> int:
    """Return the number of active HTTP-MCP sessions, or 0 if not applicable."""
    if _mcp_session_manager is None:
        return 0
    try:
        return len(_mcp_session_manager._server_instances)
    except AttributeError:
        return 0


def build_app(*, mcp_leader: bool = False) -> Starlette:
    """Build the Starlette ASGI app. Stateless — safe to call from tests.

    When ``mcp_leader`` is True, mount FastMCP's streamable-HTTP transport at
    ``/mcp`` and inherit its lifespan. Otherwise return the debugger UI alone.
    """
    global _mcp_session_manager

    routes: list[Any] = list(all_routes())

    lifespan = None
    if mcp_leader:
        from ..server import mcp as _mcp

        # The inner app's own route is at "/" so mounting it at "/mcp" puts the
        # endpoint at "/mcp" exactly (not "/mcp/mcp").
        _mcp.settings.streamable_http_path = "/"
        mcp_app = _mcp.streamable_http_app()
        routes.append(Mount("/mcp", app=mcp_app))
        # Delegate lifespan so the session manager starts with uvicorn.
        lifespan = mcp_app.router.lifespan_context
        # Capture for get_mcp_active_session_count() — path verified against
        # mcp SDK 1.27.0: routes[0].app is StreamableHTTPASGIApp.
        try:
            first_route: Any = mcp_app.routes[0]
            route_app: Any = getattr(first_route, "app", None)
            _mcp_session_manager = route_app.session_manager if route_app is not None else None
        except (AttributeError, IndexError):
            pass

    routes.extend(_frontend_routes())
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.octowright_http_host = "127.0.0.1"
    if metrics_enabled():
        app.add_middleware(HttpMetricsMiddleware)
    return app
