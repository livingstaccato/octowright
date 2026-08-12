# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Starlette app factory.

Assembles the API route list (from ``routes/``) plus the SPA frontend mount.
The frontend goes last so its catchall StaticFiles mount at ``/`` doesn't
shadow API routes.

When ``mcp_leader=True``, also mounts the MCP server's streamable-HTTP transport at
``/mcp`` and delegates lifespan to it so the session manager starts/stops with
the server. Followers connect to that endpoint instead of spawning their own
browser pool — see ``octowright.singleton`` and ``cli.serve``.
"""

from __future__ import annotations

import os
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

# --- MCP idle-session reaper config -----------------------------------------
# Seconds an MCP session may sit idle before the manager reaps it (freeing its
# per-session server task + transport), fixing the unbounded session-accumulation
# leak (see build_app). Config lives here as named consts — defaults.py is at its
# 550-LOC ceiling, so per the codebase convention subsystem knobs sit at the top
# of their own module (cf. recorder._recording_max_bytes, sysresources).
#
# OFF by default — mirrors OCTOWRIGHT_IDLE_GRACE's philosophy (defaults.py):
# killing a live client session by default is worse than a slow leak. Nothing
# pings the leader to reset a session's idle deadline between real tool calls
# (only an in-flight call's progress heartbeat does, via server/_heartbeat.py),
# so an ordinary interactive gap — the human reading output, deciding what to
# say, or the agent watching a slow build/CI run (easily 30-60+ minutes) —
# looks identical to an abandoned session to this timer. A prior default
# (first 300s, then 3600s) reaped live, wanted sessions during completely
# normal silence — there is no timeout short enough to catch a reconnect-storm
# abandoned session (which gets no activity, ever, so any positive timeout
# eventually reclaims it) without also risking a real session that just
# happens to pause exactly that long. Opt in on shared/CI hosts that want
# bounded memory over long-lived idle sessions.
MCP_SESSION_IDLE_ENV = "OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS"
MCP_SESSION_IDLE_DEFAULT = "off"
# Falsey tokens that disable reaping (restore the leaky mcp default).
MCP_SESSION_IDLE_DISABLED = frozenset({"0", "off", "never", "none", "disabled", "false", "no"})


def _mcp_session_idle_seconds(raw: str | None = None) -> float | None:
    # Read per-call so tests and the daemon both see the live env.
    raw = (raw if raw is not None else os.environ.get(MCP_SESSION_IDLE_ENV, MCP_SESSION_IDLE_DEFAULT)).strip().lower()
    if raw in MCP_SESSION_IDLE_DISABLED:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def mcp_session_manager(mcp: Any) -> Any:
    """The leader's StreamableHTTP session manager, or None when there isn't one.

    MCP 2.0 replaced the ``_session_manager`` attribute with a property that
    *raises* until ``streamable_http_app()`` has built the manager, so callers
    that used to probe the attribute (idle-timeout setup, the housekeeping
    reapers) must tolerate the raise rather than assume ``None``.
    """
    try:
        return mcp.session_manager
    except Exception:
        return None


def _apply_mcp_session_idle_timeout(mcp: Any) -> None:
    """Set the StreamableHTTP session manager's idle timeout after it's built, so
    abandoned/idle sessions get reaped instead of leaking (see build_app)."""
    seconds = _mcp_session_idle_seconds()
    if seconds is None:
        return
    manager = mcp_session_manager(mcp)
    if manager is not None:
        manager.session_idle_timeout = seconds
        log.info("octowright.mcp.session_idle_timeout_set", seconds=seconds)


def _wrap_new_session_rate_limit(app: Any) -> Any:
    """Wrap the /mcp app with the per-source new-session rate limiter, or return
    it unchanged when the limiter is disabled."""
    from octowright.http.mcp_flap_guard import (
        McpNewSessionRateLimitMiddleware,
        NewSessionRateLimiter,
        mcp_new_session_rate,
    )

    rate = mcp_new_session_rate()
    if rate is None:
        return app
    max_n, window = rate
    limiter = NewSessionRateLimiter(max_n, window)
    log.info("octowright.mcp.new_session_rate_limit_set", max=max_n, window_seconds=window)
    return McpNewSessionRateLimitMiddleware(app, limiter, retry_after=window)


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


def get_mcp_session_tracker() -> McpSessionTracker | None:
    """The current leader's session tracker (None outside a leader). Read by the
    housekeeping cap-eviction job to order eviction by last-seen activity."""
    return _session_tracker


def build_app(*, mcp_leader: bool = False, host: str = "127.0.0.1", mcp_token: str = "") -> Starlette:
    """Build the Starlette ASGI app. Stateless — safe to call from tests.

    When ``mcp_leader`` is True, mount the MCP server's streamable-HTTP transport at
    ``/mcp`` and inherit its lifespan. Otherwise return the debugger UI alone.

    ``host`` is the bind host the dashboard will serve on. It's used by the
    ASGI-mount guard, which must capture the host at wrap time because
    ``scope["app"]`` inside a Starlette ``Mount`` resolves to the inner
    mounted app rather than the outer app where ``octowright_http_host`` is
    stored.
    """
    global _session_tracker

    # Pairing credentials belong to this app instance. A new leader/app gets a
    # fresh state and therefore invalidates every prior code and bearer.
    from octowright.http.pairing import DASHBOARD_STATE_ATTR, DashboardPairingState

    dashboard_pairing = DashboardPairingState(expected_token=mcp_token)

    routes: list[Any] = list(all_routes(mcp_token=mcp_token))

    lifespan = None
    _session_tracker = None
    if mcp_leader:
        from octowright.server import mcp as _mcp

        # The inner app's own route is at "/" so mounting it at "/mcp" puts the
        # endpoint at "/mcp" exactly (not "/mcp/mcp").
        mcp_app = _mcp.streamable_http_app(streamable_http_path="/")

        # Reap abandoned/idle MCP sessions. The mcp session manager defaults
        # session_idle_timeout=None (never reap), so every session's per-session
        # server task + transport lingers in the manager's task group even after
        # the client vanishes — a real, unbounded leak (~54KB/session; a reconnect
        # storm left a leader at 2.4GB with zero live browsers). Set it after
        # construction (the manager reads it at session-create and resets the
        # deadline on each request, so an ACTIVE session is never reaped — only a
        # truly idle/abandoned one). `0`/`off` restores the leaky default.
        _apply_mcp_session_idle_timeout(_mcp)

        _session_tracker = McpSessionTracker()
        tracked_app = McpSessionTrackingMiddleware(mcp_app, _session_tracker)
        # Leader-side new-session rate limit: reject a session-creating request
        # (POST /mcp with no Mcp-Session-Id) beyond the per-source window rate
        # with 429, BEFORE it reaches the transport/tracker — so a storming
        # (usually old) follower can't churn sessions and starve the shared
        # leader. On by default; env-tunable / disable-able. See mcp_flap_guard.
        limited_app = _wrap_new_session_rate_limit(tracked_app)
        # Extract incoming W3C traceparent so spans the leader opens chain
        # under the follower's bridge span. No-ops when OTel is off.
        from octowright._trace_propagation import TraceContextExtractionMiddleware

        traced_app = TraceContextExtractionMiddleware(limited_app)
        # Capability-token auth INSIDE the host/origin guard: host/origin checked
        # first (reject non-loopback before even reading the token), then the
        # token gates the otherwise-unauthenticated /mcp transport. No-op when
        # mcp_token is "" (no token) or the env knob disables it.
        from octowright.http.bridge_auth import BridgeTokenGuard

        gated_app = guard_sensitive_asgi_app(BridgeTokenGuard(traced_app, mcp_token), host=host, side_effect_get=True)
        routes.append(Mount("/mcp", app=gated_app))
        # Delegate lifespan so the session manager starts with uvicorn.
        lifespan = mcp_app.router.lifespan_context

    # /new-tab + /otto.svg: default landing page for browser_launch with no URL.
    # Registered before the SPA catchall mount so they aren't swallowed by StaticFiles.
    # /new-tab is guarded: it server-renders the octowright version, git commit, and
    # daemon start time, so a DNS-rebinding page must not read it cross-origin — the
    # Host-header check rejects a non-loopback Host. The local browser always reaches
    # it with a loopback Host, so the landing-page UX is unchanged. /otto.svg is an
    # inert logo with no secrets, so it stays public.
    # pairing_exempt: launched browsers land on /new-tab with no pairing cookie;
    # gating it would break every browser_launch. It leaks only version/uptime/
    # browser-count — accepted, documented in http/pairing.py.
    routes.append(Route("/new-tab", guard_sensitive_http(new_tab, pairing_exempt=True), methods=["GET"]))
    routes.append(Route("/otto.svg", otto_svg, methods=["GET"]))
    routes.extend(_frontend_routes(host=host))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.octowright_http_host = host
    setattr(app.state, DASHBOARD_STATE_ATTR, dashboard_pairing)
    # provide.telemetry's ASGI middleware handles HTTP observability uniformly with
    # the rest of octowright: RED metrics (http.requests/errors/duration), request-id
    # / session-id log correlation, W3C trace propagation, and cardinality-safe route
    # normalization. Context propagation is always on; the OCTOWRIGHT_HTTP_METRICS
    # toggle only gates the RED-metrics recording (auto_slo). Read the default live so
    # tests patching defaults.HTTP_METRICS_ENABLED take effect without reimport.
    app.add_middleware(TelemetryMiddleware, auto_slo=defaults.HTTP_METRICS_ENABLED)
    return app
