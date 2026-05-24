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
    * ``pool`` / ``scenario_pool`` — the shared singletons that the MCP server
      mutates. Exposed here so HTTP-layer code only ever reads pool state
      through ``state.<name>`` (the single seam). Resolved lazily via module
      ``__getattr__`` so they always reflect the current value in
      ``octowright.server._state`` unless a test explicitly shadows them with
      ``monkeypatch.setattr(http.state, "pool", ...)``.
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
from octowright.defaults import (
    RECORDINGS_DIR,
    TAIL_HEARTBEAT_SECONDS,
    TAIL_POLL_SECONDS,
)

log = get_logger("octowright.http")

# Frontend bundle lives here (sibling subagent populates the directory).
FRONTEND_DIR = Path(__file__).parent.parent / "server" / "frontend"

# Re-exported from defaults so handlers reference `state.<NAME>`. Tests swap
# with `monkeypatch.setattr(_http.state, "X", value)`.
RECORDINGS_DIR = RECORDINGS_DIR
TAIL_POLL_SECONDS = TAIL_POLL_SECONDS
TAIL_HEARTBEAT_SECONDS = TAIL_HEARTBEAT_SECONDS


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


# ---------------------------------------------------------------------------
# Shared-singleton seam: ``pool`` and ``scenario_pool`` live on
# ``octowright.server._state`` (the MCP server owns the lifecycle). HTTP-layer
# code reads them via ``state.pool`` / ``state.scenario_pool`` so the seam is
# in one place. Module ``__getattr__`` forwards to ``_server_state`` on every
# access, so the values are always live. ``monkeypatch.setattr(state, "pool",
# X)`` plants a real attribute on this module which takes precedence over
# ``__getattr__`` for the duration of the patch (and is removed on teardown,
# restoring the forwarding behaviour). Tests that still patch
# ``server._state.pool`` continue to work because the forwarding path reads
# the current value at access time.
# ---------------------------------------------------------------------------

_FORWARDED_NAMES = frozenset({"pool", "scenario_pool"})


def __getattr__(name: str) -> Any:
    if name in _FORWARDED_NAMES:
        from octowright.server import _state as _server_state

        return getattr(_server_state, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    "pool",  # noqa: F822  resolved lazily via module __getattr__
    "runtime_session_url",
    "runtime_status",
    "runtime_url",
    "scenario_pool",  # noqa: F822  resolved lazily via module __getattr__
    "shutil",
    "subprocess",
]
