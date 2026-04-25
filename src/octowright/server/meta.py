# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Meta tools — octowright-self introspection (takeover detection, etc.).

Distinct from the gameplay tools (`browser`, `macros`, `scenarios`, …): these
tools talk *about* octowright's place in the user's MCP ecosystem rather than
driving browsers. Currently just `octowright_check_takeover`.
"""

from __future__ import annotations

from typing import Any

from .. import takeover as _takeover
from ._state import mcp


@mcp.tool(
    structured_output=False,
    description=(
        "Detect competing Playwright MCP plugins registered in the user's Claude Code "
        "config files (project-level .mcp.json and global ~/.claude.json). "
        "Returns the matching servers with scope, file path, server name, command, and "
        "the reason each one matched. WHEN TO INVOKE: call once per session if the "
        "user mentions Playwright tooling, OR when you notice you have access to both "
        "octowright tools (browser_*) AND another playwright-style toolset "
        "(e.g. mcp__playwright_*) — surface the conflict to the user, who can then run "
        "`octowright takeover` in a terminal to reversibly disable the competitors."
    ),
)
def octowright_check_takeover() -> dict[str, Any]:
    """Detect competing Playwright MCP plugins.

    Returns the Detection list structured for Claude to surface to the user.
    """
    detections = _takeover.detect_competing_servers()
    if detections:
        next_step = (
            "Run `octowright takeover` in a terminal to interactively disable them, "
            "or pass each detection's server_name to "
            "`octowright takeover --apply --scope=<scope> --name=<name>`."
        )
    else:
        next_step = "No competing playwright MCP plugins detected — octowright is already the one."

    return {
        "found": len(detections),
        "summary": _takeover.summarise(detections),
        "detections": [
            {
                "scope": d.scope,
                "config_path": str(d.config_path),
                "server_name": d.server_name,
                "command": d.command,
                "reason": d.reason,
            }
            for d in detections
        ],
        "next_step": next_step,
    }
