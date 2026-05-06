# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Demo catalog endpoints."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from octowright.demos.catalog import list_demo_bundles
from octowright.demos.indexer import build_manifest_row
from octowright.http.exposure import guard_sensitive_http


def list_demo_payloads() -> dict[str, object]:
    heroes: list[dict[str, object]] = []
    supporting: list[dict[str, object]] = []
    for bundle in list_demo_bundles():
        row = build_manifest_row(bundle)
        if bundle.hero:
            heroes.append(row)
        else:
            supporting.append(row)
    return {"heroes": heroes, "supporting": supporting}


def get_demo_payload(demo_id: str) -> dict[str, object] | None:
    for bundle in list_demo_bundles():
        if bundle.id == demo_id:
            return build_manifest_row(bundle)
    return None


def _catalog_error_response(exc: ValueError | TypeError) -> JSONResponse:
    return JSONResponse({"error": f"demo catalog unavailable: {exc}"}, status_code=500)


async def list_demos_endpoint(_request: Request) -> JSONResponse:
    try:
        return JSONResponse(list_demo_payloads())
    except (ValueError, TypeError) as exc:
        return _catalog_error_response(exc)


async def demo_detail_endpoint(request: Request) -> JSONResponse:
    demo_id = request.path_params["demo_id"]
    try:
        payload = get_demo_payload(demo_id)
    except (ValueError, TypeError) as exc:
        return _catalog_error_response(exc)
    if payload is None:
        return JSONResponse({"error": f"demo {demo_id!r} not found"}, status_code=404)
    return JSONResponse(payload)


def routes() -> list[Route]:
    return [
        Route("/api/demos", guard_sensitive_http(list_demos_endpoint), methods=["GET"]),
        Route("/api/demos/{demo_id}", guard_sensitive_http(demo_detail_endpoint), methods=["GET"]),
    ]
