# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The package-level descriptor core's loader resolves.

Everything except ``create_pool`` / ``create_scenario_adapter`` /
``session_detail`` is metadata core validates BEFORE running any of this
package's logic, which is why the class body carries no uterm import.
"""

from __future__ import annotations

from typing import Any

from octowright.plugins.contract import PLUGIN_API_VERSION

KIND = "terminal"

#: The seven tools that moved out of core's `server/terminal/lifecycle.py`.
#: Declared here and registered by importing `tool_module`; core refuses the
#: plugin at validation if any name collides with a core tool.
TOOL_NAMES = frozenset(
    {
        "terminal_launch",
        "terminal_send_input",
        "terminal_snapshot",
        "terminal_read",
        "terminal_wait_for",
        "terminal_close",
        "terminal_list",
    }
)


class TerminalPlugin:
    kind = KIND
    display_name = "Terminal"
    plugin_api_version = PLUGIN_API_VERSION
    tool_names = TOOL_NAMES
    tool_module = "octowright_terminal.tools"
    profile_name = "terminals"
    frontend = None  # Task 7

    def create_pool(self, ctx: Any) -> Any:
        from octowright_terminal.pool import TerminalPool

        return TerminalPool(ctx)

    def create_scenario_adapter(self, _pool: Any) -> Any:
        return None  # Task 5

    def session_detail(self, _session: Any) -> dict[str, Any]:
        return {}  # Task 6


plugin = TerminalPlugin()
