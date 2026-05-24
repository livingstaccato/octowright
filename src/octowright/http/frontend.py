# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Frontend (SPA) routes for the HTTP debugger sidecar."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from octowright.http import state
from octowright.http.exposure import guard_sensitive_asgi_app, guard_sensitive_http


async def _serve_session_html(_: Request) -> Response:
    """SPA fallback for /sessions/<id> deep-links — serves session.html.

    The frontend reads the id from window.location.pathname. Without this
    fallback, StaticFiles 404s because there's no `sessions/<id>` file.
    """
    target = state.FRONTEND_DIR / "session.html"
    if not target.exists():
        return PlainTextResponse("session.html not bundled (run npm run build)", status_code=404)
    return FileResponse(str(target), media_type="text/html")


def _frontend_routes(*, host: str = "127.0.0.1") -> list[Any]:
    """Routes that serve the bundled SPA at `/`.

    Adds an explicit `/sessions/{id}` route so deep-links resolve to
    session.html (the StaticFiles mount alone can't do SPA-style routing).
    The catchall mount at `/` handles index.html and every static asset.

    Both routes are wrapped in the bind-host guard so a non-loopback bind
    with ``OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD`` unset stops returning a 200
    SPA page (which would confirm the daemon and expose the dashboard
    surface). ``host`` is captured at route-build time because the SPA
    StaticFiles mount is an ASGI app, not a Request handler — the ASGI
    guard reads bind host from a closure.

    When the bundle isn't there yet (first-run, dev), the API still works —
    the dashboard is just blank.
    """
    if not (state.FRONTEND_DIR.exists() and state.FRONTEND_DIR.is_dir()):
        return []
    static_app = StaticFiles(directory=str(state.FRONTEND_DIR), html=True)
    return [
        Route("/sessions/{id:path}", guard_sensitive_http(_serve_session_html), methods=["GET"]),
        Mount("/", app=guard_sensitive_asgi_app(static_app, host=host), name="frontend"),
    ]
