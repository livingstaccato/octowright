# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``GET /api/health`` — liveness + version probe."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


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
    # HTTP metrics are emitted through provide.telemetry's TelemetryMiddleware
    # (RED metrics → OTLP) in http.app, not a bespoke Prometheus scrape endpoint.
    return [
        # intentionally unguarded — used by liveness probes / load balancers; exposes only {ok, version}
        Route("/api/health", health_endpoint, methods=["GET"]),
    ]
