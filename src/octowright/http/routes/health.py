# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``GET /api/health`` — liveness + version probe.

``version`` is the version of the code THIS PROCESS IS RUNNING.

That sounds obvious; the tempting alternative is not. Calling
``importlib.metadata.version("octowright")` on every request reads dist-info
off disk, on the stated intent that "a ``uv pip install --upgrade`` is reflected
without a server restart". But an upgrade on disk does not change a running
process: the daemon keeps executing the code it imported until it is restarted.
So the one question an operator asks this endpoint after deploying -- "is the
daemon running the new version yet?" -- was the one it could never answer
correctly, because it always reported the newest thing installed. Observed
2026-08-20: a leader started the previous evening reported the version a
``uv sync`` had written to disk seconds earlier.

The upgrade-detection intent was still worth something, so it is kept as a
SEPARATE, honestly-named field: ``installed_version`` appears only when the
on-disk package differs from the running one, which is exactly the "restart to
pick this up" signal the original comment was reaching for.

Reading that metadata is a dist-info scan, and every follower probes this
endpoint every couple of seconds on the leader's event loop, so the on-disk
version is re-read at most once per ``INSTALLED_VERSION_TTL_SECONDS``. An
install lands on disk at human speed and a restart takes longer than that, so a
reading up to that old still answers "restart to pick this up" correctly.
"""

from __future__ import annotations

import time

from starlette.requests import Request
from starlette.routing import Route

from octowright.http.json_response import SafeJSONResponse
from octowright.version import VERSION

# How long one reading of the on-disk version is reused (see the module docstring).
INSTALLED_VERSION_TTL_SECONDS = 30.0

# (monotonic time read, value); None until the first request.
_installed_cache: tuple[float, str | None] | None = None


def _read_installed_version() -> str | None:
    """Version recorded in on-disk package metadata, or ``None`` if unreadable."""
    try:
        from importlib.metadata import version

        return version("octowright")
    except Exception:
        return None


def _installed_version() -> str | None:
    """The on-disk version, re-read at most once per ``INSTALLED_VERSION_TTL_SECONDS``."""
    global _installed_cache
    now = time.monotonic()
    cached = _installed_cache
    if cached is not None and now - cached[0] < INSTALLED_VERSION_TTL_SECONDS:
        return cached[1]
    value = _read_installed_version()
    _installed_cache = (now, value)
    return value


async def health_endpoint(_request: Request) -> SafeJSONResponse:
    payload: dict[str, object] = {"ok": True, "version": VERSION}
    installed = _installed_version()
    if installed is not None and installed != VERSION:
        # Only when they disagree, so the ordinary response shape is unchanged.
        payload["installed_version"] = installed
    return SafeJSONResponse(payload)


def routes() -> list[Route]:
    # HTTP metrics are emitted through provide.telemetry's TelemetryMiddleware
    # (RED metrics → OTLP) in http.app, not a bespoke Prometheus scrape endpoint.
    return [
        # intentionally unguarded — used by liveness probes / load balancers; exposes only {ok, version}
        Route("/api/health", health_endpoint, methods=["GET"]),
    ]
