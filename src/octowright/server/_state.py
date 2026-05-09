# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared singletons for the MCP server: the FastMCP instance, browser/scenario
pools, and the logger. Submodules import from here to register tools against
the same `mcp` and to share the same live state."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from provide.telemetry import get_logger

from octowright import scenarios as _scenarios
from octowright.browser_pool import BrowserPool
from octowright.server.profiles import active_filter

log = get_logger("octowright.server")

pool = BrowserPool()
scenario_pool = _scenarios.ScenarioPool()
mcp = FastMCP(
    "octowright",
    instructions=(
        "Launch and drive multiple headed Playwright browsers in parallel. "
        "Each browser has an instance_id; pass it to every per-browser tool. "
        "Every action is recorded to a JSONL log that can be exported as a Playwright script. "
        "Use the `profile` arg on browser_launch to persist cookies/localStorage/IndexedDB across runs."
    ),
)

# Capability-profile filter (OCTOWRIGHT_PROFILE). Wraps mcp.tool so any tool
# whose name is not in the active allow-list is skipped at decoration time
# rather than registered with FastMCP. Zero overhead when no profile is set.
_allowed_tools = active_filter()
if _allowed_tools is not None:
    _allowed: set[str] = _allowed_tools
    _original_tool = mcp.tool

    def _filtered_tool(*args: Any, **kwargs: Any) -> Any:
        decorator = _original_tool(*args, **kwargs)

        def wrap(fn: Any) -> Any:
            if fn.__name__ not in _allowed:
                return fn
            return decorator(fn)

        return wrap

    mcp.tool = _filtered_tool  # type: ignore[method-assign]
    log.info("octowright.profile.active", allowed=sorted(_allowed))
