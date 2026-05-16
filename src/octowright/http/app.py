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

from provide.telemetry import get_logger
from starlette.applications import Starlette
from starlette.routing import Mount

from octowright.http.exposure import guard_sensitive_asgi_app
from octowright.http.frontend import _frontend_routes
from octowright.http.mcp_session_tracker import (
    McpSessionTracker,
    McpSessionTrackingMiddleware,
)
from octowright.http.metrics import HttpMetricsMiddleware, metrics_enabled
from octowright.http.routes import all_routes

log = get_logger(__name__)

# Tracker covering active streamable-HTTP MCP sessions; reset on every
# build_app() so the count belongs to the most recently built leader app.
# Idle watchdog reads through get_mcp_active_session_count() so it doesn't
# exit while followers are connected.
_session_tracker: McpSessionTracker | None = None


def get_mcp_active_session_count() -> int:
    """Return the number of active HTTP-MCP sessions, or 0 if not applicable."""
    if _session_tracker is None:
        return 0
    return _session_tracker.active_count()


def build_app(*, mcp_leader: bool = False) -> Starlette:
    """Build the Starlette ASGI app. Stateless — safe to call from tests.

    When ``mcp_leader`` is True, mount FastMCP's streamable-HTTP transport at
    ``/mcp`` and inherit its lifespan. Otherwise return the debugger UI alone.
    """
    global _session_tracker

    routes: list[Any] = list(all_routes())

    lifespan = None
    _session_tracker = None
    if mcp_leader:
        from octowright.server import mcp as _mcp

        # The inner app's own route is at "/" so mounting it at "/mcp" puts the
        # endpoint at "/mcp" exactly (not "/mcp/mcp").
        _mcp.settings.streamable_http_path = "/"
        mcp_app = _mcp.streamable_http_app()

        _session_tracker = McpSessionTracker()
        tracked_app = McpSessionTrackingMiddleware(mcp_app, _session_tracker)
        routes.append(Mount("/mcp", app=guard_sensitive_asgi_app(tracked_app)))
        # Delegate lifespan so the session manager starts with uvicorn.
        lifespan = mcp_app.router.lifespan_context

    routes.extend(_frontend_routes())
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.octowright_http_host = "127.0.0.1"
    if metrics_enabled():
        app.add_middleware(HttpMetricsMiddleware)
    return app
