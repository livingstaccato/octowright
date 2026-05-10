# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Minimal HTTP metrics for debugger/API endpoints."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from octowright import defaults


@dataclass
class _Latency:
    count: int = 0
    total_seconds: float = 0.0


class HttpMetrics:
    """In-memory process-local HTTP counters/latency buckets."""

    def __init__(self) -> None:
        self.requests_total = 0
        self.requests_by_status: dict[str, int] = defaultdict(int)
        self.requests_by_route: dict[str, int] = defaultdict(int)
        self.latency_by_route: dict[str, _Latency] = defaultdict(_Latency)

    def observe(self, route: str, status_code: int, elapsed_seconds: float) -> None:
        self.requests_total += 1
        self.requests_by_status[str(status_code)] += 1
        self.requests_by_route[route] += 1
        slot = self.latency_by_route[route]
        slot.count += 1
        slot.total_seconds += elapsed_seconds

    def render_prometheus(self) -> str:
        lines: list[str] = [
            "# HELP octowright_http_requests_total Total HTTP requests.",
            "# TYPE octowright_http_requests_total counter",
            f"octowright_http_requests_total {self.requests_total}",
            "# HELP octowright_http_requests_by_status Total HTTP requests by status code.",
            "# TYPE octowright_http_requests_by_status counter",
        ]
        for code, count in sorted(self.requests_by_status.items()):
            lines.append(f'octowright_http_requests_by_status{{status="{code}"}} {count}')
        lines.extend(
            [
                "# HELP octowright_http_requests_by_route Total HTTP requests by route pattern.",
                "# TYPE octowright_http_requests_by_route counter",
            ]
        )
        for route, count in sorted(self.requests_by_route.items()):
            lines.append(f'octowright_http_requests_by_route{{route="{route}"}} {count}')
        lines.extend(
            [
                "# HELP octowright_http_request_duration_seconds_sum Cumulative request duration in seconds by route.",
                "# TYPE octowright_http_request_duration_seconds_sum counter",
                "# HELP octowright_http_request_duration_seconds_count Request count used for duration averages by route.",
                "# TYPE octowright_http_request_duration_seconds_count counter",
            ]
        )
        for route, slot in sorted(self.latency_by_route.items()):
            lines.append(f'octowright_http_request_duration_seconds_sum{{route="{route}"}} {slot.total_seconds:.6f}')
            lines.append(f'octowright_http_request_duration_seconds_count{{route="{route}"}} {slot.count}')
        return "\n".join(lines) + "\n"


HTTP_METRICS = HttpMetrics()


def metrics_enabled() -> bool:
    """Live-read from defaults so test patches via
    ``monkeypatch.setattr(defaults, 'HTTP_METRICS_ENABLED', False)`` take
    effect without reimporting this module."""
    return defaults.HTTP_METRICS_ENABLED


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    """Record per-request counters and latency for HTTP endpoints."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        route_pattern = request.url.path
        route_obj = request.scope.get("route")
        if route_obj is not None:
            route_pattern = getattr(route_obj, "path", route_pattern)
        HTTP_METRICS.observe(route_pattern, int(getattr(response, "status_code", 500)), time.perf_counter() - started)
        return response
