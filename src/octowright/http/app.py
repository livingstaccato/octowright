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
from .routes import all_routes


def build_app(*, mcp_leader: bool = False) -> Starlette:
    """Build the Starlette ASGI app. Stateless — safe to call from tests.

    When ``mcp_leader`` is True, mount FastMCP's streamable-HTTP transport at
    ``/mcp`` and inherit its lifespan. Otherwise return the debugger UI alone.
    """
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

    routes.extend(_frontend_routes())
    return Starlette(routes=routes, lifespan=lifespan)
