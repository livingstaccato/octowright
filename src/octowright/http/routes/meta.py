# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Meta endpoints: personas / macros listings."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import state


async def list_personas_endpoint(_request: Request) -> JSONResponse:
    rows = state._personas.list_personas()
    out = [
        {
            "name": r["name"],
            "display_name": r.get("display_name"),
            "engines": r.get("engines", []),
            "last_used": r.get("last_used"),
        }
        for r in rows
    ]
    return JSONResponse(out)


async def list_macros_endpoint(_request: Request) -> JSONResponse:
    rows = state._macros.list_macros()
    out = [
        {
            "name": r["name"],
            "description": r.get("description"),
            "parameters": r.get("parameters", []),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]
    return JSONResponse(out)


def routes() -> list[Route]:
    return [
        Route("/api/personas", list_personas_endpoint, methods=["GET"]),
        Route("/api/macros", list_macros_endpoint, methods=["GET"]),
    ]
