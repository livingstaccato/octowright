# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``GET /api/health`` — liveness + version probe."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from octowright.http.exposure import guard_sensitive_http
from octowright.http.metrics import HTTP_METRICS, metrics_enabled


async def health_endpoint(_request: Request) -> JSONResponse:
    # Version pulled from package metadata at request time so a `pip install
    # --upgrade` is reflected without a server restart.
    try:
        from importlib.metadata import version

        ver = version("octowright")
    except Exception:
        ver = "unknown"
    return JSONResponse({"ok": True, "version": ver})


def routes() -> list[Route]:
    out: list[Route] = [
        # intentionally unguarded — used by liveness probes / load balancers; exposes only {ok, version}
        Route("/api/health", health_endpoint, methods=["GET"]),
    ]
    if metrics_enabled():
        out.append(Route("/api/metrics", guard_sensitive_http(metrics_endpoint), methods=["GET"]))
    return out


async def metrics_endpoint(_request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        HTTP_METRICS.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
