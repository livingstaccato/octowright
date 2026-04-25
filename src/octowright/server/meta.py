# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Meta tools — octowright-self introspection (takeover detection, dashboard URL).

Distinct from the gameplay tools (`browser`, `macros`, `scenarios`, …): these
tools talk *about* octowright (its UI, its MCP ecosystem) rather than driving
browsers.
"""

from __future__ import annotations

from typing import Any

from .. import takeover as _takeover
from ._state import mcp, pool, scenario_pool


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


@mcp.tool(
    structured_output=False,
    description=(
        "Returns the URL of octowright's web debugger dashboard. The URL is a "
        "localhost web app showing every live browser, every scenario, "
        "recordings/screenshots/videos/traces, plus a per-session debugger with "
        "action timeline + embedded video. Use this whenever the user asks "
        "'show me what's happening' or wants to debug a session — it's far "
        "better than calling browser_recording_path one at a time. "
        "Pass `session_id` to get a deep-link to a specific session's debugger page."
    ),
)
def octowright_dashboard_url(session_id: str | None = None) -> dict[str, Any]:
    """Return the dashboard URL (and optional session deep-link).

    Reports `running: false` with an `error` field when the HTTP sidecar
    failed to bind (e.g., port collision, sidecar not started).
    """
    from .. import http_server as _http

    status = _http.runtime_status()
    base_url = _http.runtime_url()
    deep = _http.runtime_session_url(session_id) if session_id else None
    closed_count = 0
    live_count = len(pool._sessions)
    try:
        from ..defaults import RECORDINGS_DIR

        if RECORDINGS_DIR.exists():
            closed_count = sum(1 for _ in RECORDINGS_DIR.glob("*.jsonl"))
    except Exception:
        closed_count = 0

    result: dict[str, Any] = {
        "url": base_url,
        "session_url": deep,
        "live_sessions": live_count,
        "closed_sessions": closed_count,
        "running": status["running"],
        "live_scenarios": len(scenario_pool.list_live()),
    }
    if not status["running"] and status.get("error"):
        result["error"] = status["error"]
    elif not status["running"]:
        result["error"] = "HTTP debugger sidecar not running (call `octowright serve` to start it)"
    return result
