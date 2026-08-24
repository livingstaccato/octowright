# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The renderer registry the dashboard reads.

One row per kind that actually declares a frontend; a kind without one is
absent rather than present-and-null, so the SPA's check is a lookup miss rather
than a null test.

``moduleUrl`` is joined here, not in the SPA. Core owns every URL a plugin is
reachable at, for the same reason it owns artifact path composition: the one
place that builds it is the one place that has to be right.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from octowright.http.exposure import guard_sensitive_http


def _frontend_rows() -> dict[str, dict[str, Any]]:
    from octowright.plugins.state import registry

    reg = registry()
    by_kind: dict[str, dict[str, Any]] = {}
    names_by_kind = {row.get("kind"): row.get("name") for row in reg.status_rows() if row.get("kind")}
    for kind in reg.kinds():
        descriptor = reg.get_plugin(kind).descriptor
        frontend = descriptor.frontend
        if frontend is None:
            continue
        name = names_by_kind.get(kind)
        if not name:
            continue
        by_kind[kind] = {
            "moduleUrl": f"/plugins/{name}/{frontend.module_path}",
            "rendererApiVersion": frontend.renderer_api_version,
            "displayName": descriptor.display_name,
            "layout": frontend.layout,
        }
    return by_kind


async def list_plugin_frontends(_request: Request) -> JSONResponse:
    """GET /api/plugins — kind → renderer descriptor."""
    return JSONResponse(_frontend_rows())


def plugins_api_routes() -> list[Route]:
    # pairing_exempt for the same reason the asset route is: the shell reads this
    # to decide what to import, before pairing has necessarily completed. It
    # exposes only what an operator already enabled -- no session data.
    return [Route("/api/plugins", guard_sensitive_http(list_plugin_frontends, pairing_exempt=True), methods=["GET"])]
