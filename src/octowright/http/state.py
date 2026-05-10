# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared mutable module-level state for the HTTP debugger sidecar.

Handlers in ``octowright.http.routes.*`` look up these names through
``state.<NAME>`` so that tests can ``monkeypatch.setattr(_http.state, "X", ...)``
and have every handler observe the patched value.

Holds:
    * ``FRONTEND_DIR`` — directory where the bundled SPA lives.
    * ``RECORDINGS_DIR`` — directory where JSONL/video/trace artefacts land.
      Re-exported from ``octowright.defaults`` so tests can swap it per-test.
    * ``TAIL_POLL_SECONDS`` — WS tail loop frequency.
    * ``_RUNTIME_HOST`` / ``_RUNTIME_PORT`` / ``_RUNTIME_ERROR`` — populated
      by ``lifespan.serve_app()`` once uvicorn binds; cleared on shutdown.
      Read by ``runtime_url()`` and the ``octowright_dashboard_url`` MCP tool.
    * Module-import handles for the helpers tests like to swap (``_video``,
      ``_personas``, ``_macros``, ``shutil``, ``subprocess``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright import macros as _macros
from octowright import personas as _personas
from octowright import video as _video
from octowright.defaults import RECORDINGS_DIR

log = get_logger("octowright.http")

# Frontend bundle lives here (sibling subagent populates the directory).
FRONTEND_DIR = Path(__file__).parent.parent / "server" / "frontend"

# Polling interval for the WS /tail endpoint. ~1 Hz feels live without
# hammering the file system.
TAIL_POLL_SECONDS = 1.0

# Maximum gap between WS tail heartbeats. The tail loop only sends a frame
# when there's something new (events, or a live→closed transition); this
# bounds how long a quiet stream can stay silent before sending an empty
# keepalive so the dashboard knows the connection's still alive.
TAIL_HEARTBEAT_SECONDS = 15.0

# Re-exported so handlers reference `state.RECORDINGS_DIR`. Tests swap with
# `monkeypatch.setattr(_http.state, "RECORDINGS_DIR", tmp_path)`.
RECORDINGS_DIR = RECORDINGS_DIR


# ---------------------------------------------------------------------------
# Module-level state used by the dashboard MCP tool to discover the bound port.
# Populated by `serve_app()` once uvicorn binds; cleared on shutdown.
# ---------------------------------------------------------------------------

_RUNTIME_HOST: str | None = None
_RUNTIME_PORT: int | None = None
_RUNTIME_ERROR: str | None = None


def runtime_url() -> str | None:
    """Return the dashboard URL the HTTP server is bound to, or None if not running."""
    if _RUNTIME_HOST is None or _RUNTIME_PORT is None:
        return None
    return f"http://{_RUNTIME_HOST}:{_RUNTIME_PORT}/"


def runtime_session_url(session_id: str) -> str | None:
    base = runtime_url()
    if base is None:
        return None
    return f"{base}sessions/{session_id}"


def runtime_status() -> dict[str, Any]:
    """Snapshot used by the `octowright_dashboard_url` MCP tool."""
    return {
        "running": _RUNTIME_HOST is not None and _RUNTIME_PORT is not None,
        "host": _RUNTIME_HOST,
        "port": _RUNTIME_PORT,
        "error": _RUNTIME_ERROR,
    }


__all__ = [
    "FRONTEND_DIR",
    "RECORDINGS_DIR",
    "TAIL_HEARTBEAT_SECONDS",
    "TAIL_POLL_SECONDS",
    "_RUNTIME_ERROR",
    "_RUNTIME_HOST",
    "_RUNTIME_PORT",
    "_macros",
    "_personas",
    "_video",
    "log",
    "runtime_session_url",
    "runtime_status",
    "runtime_url",
    "shutil",
    "subprocess",
]
