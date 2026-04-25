# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared singletons for the MCP server: the FastMCP instance, browser/scenario
pools, and the logger. Submodules import from here to register tools against
the same `mcp` and to share the same live state."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from provide.telemetry import get_logger

from .. import scenarios as _scenarios
from ..pool import BrowserPool

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
