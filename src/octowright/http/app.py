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

from provide.telemetry import TelemetryMiddleware, get_logger
from starlette.applications import Starlette
from starlette.routing import Mount, Route

from octowright import defaults
from octowright.http.exposure import guard_sensitive_asgi_app, guard_sensitive_http
from octowright.http.frontend import _frontend_routes
from octowright.http.mcp_session_tracker import (
    McpSessionTracker,
    McpSessionTrackingMiddleware,
)
from octowright.http.routes import all_routes
from octowright.http.routes.new_tab import new_tab, otto_svg

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


def build_app(*, mcp_leader: bool = False, host: str = "127.0.0.1") -> Starlette:
    """Build the Starlette ASGI app. Stateless — safe to call from tests.

    When ``mcp_leader`` is True, mount FastMCP's streamable-HTTP transport at
    ``/mcp`` and inherit its lifespan. Otherwise return the debugger UI alone.

    ``host`` is the bind host the dashboard will serve on. It's used by the
    ASGI-mount guard, which must capture the host at wrap time because
    ``scope["app"]`` inside a Starlette ``Mount`` resolves to the inner
    mounted app rather than the outer app where ``octowright_http_host`` is
    stored.
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
        # Extract incoming W3C traceparent so spans the leader opens chain
        # under the follower's bridge span. No-ops when OTel is off.
        from octowright._trace_propagation import TraceContextExtractionMiddleware

        traced_app = TraceContextExtractionMiddleware(tracked_app)
        routes.append(
            Mount(
                "/mcp",
                app=guard_sensitive_asgi_app(traced_app, host=host, side_effect_get=True),
            )
        )
        # Delegate lifespan so the session manager starts with uvicorn.
        lifespan = mcp_app.router.lifespan_context

    # /new-tab + /otto.svg: default landing page for browser_launch with no URL.
    # Registered before the SPA catchall mount so they aren't swallowed by StaticFiles.
    # /new-tab is guarded: it server-renders the octowright version, git commit, and
    # daemon start time, so a DNS-rebinding page must not read it cross-origin — the
    # Host-header check rejects a non-loopback Host. The local browser always reaches
    # it with a loopback Host, so the landing-page UX is unchanged. /otto.svg is an
    # inert logo with no secrets, so it stays public.
    routes.append(Route("/new-tab", guard_sensitive_http(new_tab), methods=["GET"]))
    routes.append(Route("/otto.svg", otto_svg, methods=["GET"]))
    routes.extend(_frontend_routes(host=host))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.octowright_http_host = host
    # provide.telemetry's ASGI middleware handles HTTP observability uniformly with
    # the rest of octowright: RED metrics (http.requests/errors/duration), request-id
    # / session-id log correlation, W3C trace propagation, and cardinality-safe route
    # normalization. Context propagation is always on; the OCTOWRIGHT_HTTP_METRICS
    # toggle only gates the RED-metrics recording (auto_slo). Read the default live so
    # tests patching defaults.HTTP_METRICS_ENABLED take effect without reimport.
    app.add_middleware(TelemetryMiddleware, auto_slo=defaults.HTTP_METRICS_ENABLED)
    return app
