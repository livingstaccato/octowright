# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Frontend (SPA) routes for the HTTP debugger sidecar."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from octowright.frontend_bundle import BUILD_COMMAND
from octowright.http import state
from octowright.http.exposure import guard_sensitive_asgi_app, guard_sensitive_http

_NOT_BUILT_HTML = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>octowright: dashboard not built</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem}}
code{{background:#8882;padding:.1rem .3rem;border-radius:3px}}</style></head>
<body><h1>The octowright dashboard is not built</h1>
<p>This daemon is running from a source checkout whose dashboard bundle has not been
compiled, so there is nothing to serve here. The MCP tools and the API work without it.</p>
<p>From the checkout's root, run:</p>
<p><code>{BUILD_COMMAND}</code></p>
<p>then restart the daemon (<code>octowright restart</code>); it looks for the bundle at startup.
<code>octowright doctor</code> reports the same thing.</p></body></html>
"""


async def _serve_not_built(request: Request) -> Response:
    """The dashboard's paths when the bundle is missing: say so, and how to fix it.

    An unknown ``/api/`` path keeps its ordinary 404: it is a client error, not a
    missing dashboard, and an API caller should not be handed an HTML page.
    """
    if request.url.path.startswith("/api/"):
        return PlainTextResponse("Not Found", status_code=404)
    return HTMLResponse(_NOT_BUILT_HTML, status_code=404)


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

    When the bundle isn't there yet (a source checkout that never ran the
    build), the API still works and every dashboard path answers with a page
    naming `BUILD_COMMAND` -- a bare "Not Found" sent people to the source.
    """
    if not (state.FRONTEND_DIR.exists() and state.FRONTEND_DIR.is_dir()):
        return [
            Route(
                "/{path:path}",
                guard_sensitive_http(_serve_not_built, pairing_exempt=True),
                methods=["GET"],
            )
        ]
    static_app = StaticFiles(directory=str(state.FRONTEND_DIR), html=True)
    return [
        # HTML/static bootstrap stays public under the loopback Host/origin
        # boundary. Data and media requests made by the SPA carry the bearer.
        Route(
            "/sessions/{id:path}",
            guard_sensitive_http(_serve_session_html, pairing_exempt=True),
            methods=["GET"],
        ),
        Mount("/", app=guard_sensitive_asgi_app(static_app, host=host), name="frontend"),
    ]
