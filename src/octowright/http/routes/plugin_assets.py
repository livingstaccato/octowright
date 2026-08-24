# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Static assets a session-kind plugin ships for the dashboard.

Addressed by the plugin's ENTRY-POINT NAME rather than its kind: the name is the
configured identity an operator writes in ``OCTOWRIGHT_PLUGINS``, and unlike a
kind it may contain a hyphen, which suits a URL segment.

Gated like the static SPA mount, NOT like the session APIs. These are static
files from an operator-enabled package carrying no session data, and the
dashboard shell has to boot before pairing completes -- gating them would leave
a paired dashboard unable to load the code that renders its own panes.

``{path}`` is caller-supplied, so it goes through the same resolve-then-contain
discipline every other caller-influenced path in this codebase uses, with
symlinks resolved before the prefix check.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from octowright._paths import reject_unsafe_path
from octowright.http.exposure import guard_sensitive_http

# Pin the JavaScript module types rather than trusting the host's mimetype
# database. Python's `mimetypes` seeds itself from the Windows registry, where
# `.mjs` is typically absent -- so a Windows-hosted daemon served a plugin's
# renderer as `application/octet-stream`, and browsers enforce strict MIME
# checking on ES modules and REFUSE to execute one that isn't a JavaScript
# type. The module loaded fine on Linux and macOS and failed only on Windows,
# which is exactly the class of bug that reaches a user before it reaches us.
# Registering both extensions makes the served type deterministic everywhere.
for _js_suffix in (".js", ".mjs"):
    mimetypes.add_type("text/javascript", _js_suffix)

#: Extensions the dashboard can actually use. Deliberately closed for the same
#: reason ARTIFACT_MIME_ALLOWLIST is: this route serves from the dashboard's own
#: origin, so anything it hands back runs beside the pairing bearer.
_ASSET_SUFFIXES: frozenset[str] = frozenset({".js", ".mjs", ".css", ".map", ".woff2", ".svg", ".png"})


def _asset_dir_for(name: str) -> Path | None:
    """The declared asset directory for entry-point ``name``, or None."""
    from octowright.plugins.state import registry

    reg = registry()
    for row in reg.status_rows():
        if row.get("name") != name:
            continue
        kind = row.get("kind")
        if not isinstance(kind, str) or kind not in reg.kinds():
            return None
        frontend = reg.get_plugin(kind).descriptor.frontend
        return None if frontend is None else Path(frontend.asset_dir)
    return None


async def plugin_asset(request: Request) -> Response:
    """GET /plugins/{name}/{path} — serve one file from a plugin's asset dir."""
    name = request.path_params["name"]
    rel = request.path_params["path"]

    asset_dir = _asset_dir_for(name)
    if asset_dir is None:
        return JSONResponse({"error": "no such plugin frontend"}, status_code=404)

    candidate = asset_dir / rel
    if candidate.suffix not in _ASSET_SUFFIXES:
        return JSONResponse({"error": f"asset type {candidate.suffix!r} is not served"}, status_code=404)
    try:
        resolved = reject_unsafe_path(candidate, asset_dir, label="plugin asset")
    except ValueError:
        return JSONResponse({"error": "asset path escapes the plugin's asset dir"}, status_code=404)
    if not resolved.is_file():
        return JSONResponse({"error": "no such asset"}, status_code=404)
    # No `filename=`: that makes FileResponse send `Content-Disposition:
    # attachment`, which downloads the file when opened directly instead of
    # displaying it -- meaningless for something only ever consumed via
    # `import()` (browsers ignore Content-Disposition for module fetches, so
    # this had no effect on loading; it only broke opening the URL to debug
    # it). Python's `mimetypes` has no `.map` entry, so a source map is served
    # as `application/octet-stream` -- harmless, DevTools reads it regardless,
    # and unlike a module a source map is not MIME-checked.
    return FileResponse(path=str(resolved))


def plugin_asset_routes() -> list[Route]:
    # pairing_exempt: see the module docstring -- the shell must boot before
    # pairing completes, and these files carry no session data.
    return [
        Route(
            "/plugins/{name}/{path:path}",
            guard_sensitive_http(plugin_asset, pairing_exempt=True),
            methods=["GET"],
        )
    ]
