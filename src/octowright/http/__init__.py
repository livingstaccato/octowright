# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""HTTP debugger sidecar.

A Starlette application that exposes the live ``BrowserPool``/``ScenarioPool``
state plus on-disk recordings/screenshots/videos/traces to a single-page
frontend. Designed to run alongside the MCP stdio server in the same event
loop (see ``cli/serve.py``).

Endpoints (mirror the API contract in MCP-SHARED-CONTRACT.md):

    GET  /                                         → static index.html
    GET  /sessions/{id}                            → static session.html
    GET  /api/sessions                             → live + closed session lists
    POST /api/sessions                             → launch a new browser session
    GET  /api/sessions/{id}                        → SessionDetail
    DEL  /api/sessions/{id}                        → close a live session
    POST /api/sessions/{id}/navigate               → drive page to {url}
    GET  /api/sessions/{id}/events?since=N         → tail JSONL events
    GET  /api/dashboard/events                     → SSE dashboard invalidations
    GET  /api/sessions/{id}/console?level=&since=N → console messages (paginated)
    GET  /api/sessions/{id}/downloads?since=N      → downloads (paginated)
    WS   /api/sessions/{id}/tail                   → push events ~1Hz (LIVE only;
                                                     closed/unknown → immediate close)
    GET  /api/sessions/{id}/frame?t=<sec>          → ffmpeg-extracted PNG (cached)
    GET  /api/sessions/{id}/video                  → video bytes (range supported)
    GET  /api/sessions/{id}/trace                  → trace .zip
    GET  /api/sessions/{id}/screenshot/now         → live screenshot (PNG/JPEG)
    GET  /api/sessions/{id}/screenshots            → list screenshots
    GET  /api/sessions/{id}/screenshots/{file}     → screenshot PNG bytes
    POST /api/sessions/{id}/trace/open             → spawn ``npx playwright show-trace``
    GET  /api/scenarios                            → live scenarios
    POST /api/scenarios/{name}/start               → start a scenario by name
    DEL  /api/scenarios/{id}                       → stop a live scenario
    POST /api/scenarios/{id}/run_macro             → broadcast a macro to a scenario
    GET  /api/personas                             → persona summaries
    GET  /api/macros                               → macro summaries
    GET  /api/health                               → liveness probe

State is read through the HTTP-layer seam ``octowright.http.state`` —
``state.pool`` and ``state.scenario_pool`` forward to the same shared
singletons that the MCP tools mutate (defined in ``octowright.server._state``).
Closed sessions are reconstructed from ``RECORDINGS_DIR/*.jsonl``.

Module-level mutables (``RECORDINGS_DIR``, ``FRONTEND_DIR``, runtime port,
etc.) live in ``state``. Tests should monkeypatch via ``_http.state.X``.
"""

from __future__ import annotations

from octowright.defaults import DEFAULT_URL
from octowright.http.app import build_app, get_mcp_active_session_count
from octowright.http.lifespan import serve_app
from octowright.http.state import (
    FRONTEND_DIR,
    RECORDINGS_DIR,
    runtime_session_url,
    runtime_status,
    runtime_url,
)

__all__ = [
    "DEFAULT_URL",
    "FRONTEND_DIR",
    "RECORDINGS_DIR",
    "build_app",
    "get_mcp_active_session_count",
    "runtime_session_url",
    "runtime_status",
    "runtime_url",
    "serve_app",
]
