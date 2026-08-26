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

from pathlib import Path
from typing import Any

from octowright.plugins.contract import FrontendAsset

KIND = "terminal"

#: The terminal plugin's dashboard renderer -- a self-contained bundle (xterm
#: + two addons inlined, see assets-src/build.mjs) built from
#: assets-src/src/renderer.ts and committed here because a Python wheel has
#: no npm step at install time. See ../../assets-src/README.md for how to
#: rebuild it.
_ASSET_DIR = Path(__file__).resolve().parent / "assets"

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
    #: A LITERAL, deliberately -- not core's ``PLUGIN_API_VERSION``. This is the
    #: version of the backend contract this package was written against, and an
    #: independently released distribution can be installed beside a core it has
    #: never been built with. Importing core's constant makes it agree by
    #: construction, which is the same as having no gate: a core bump would be
    #: silently auto-adopted and the plugin would fail later, at whatever
    #: Protocol actually changed, instead of being refused at load with the
    #: legible message the loader already produces. The renderer half of this
    #: descriptor already states its version as a literal for the same reason.
    #: `tests/test_entry_point.py` fails when this and core's constant diverge,
    #: so a core bump forces a deliberate decision here rather than a surprise.
    plugin_api_version = 1
    tool_names = TOOL_NAMES
    tool_module = "octowright_terminal.tools"
    profile_name = "terminals"
    frontend = FrontendAsset(
        renderer_api_version=1,
        asset_dir=_ASSET_DIR,
        module_path="renderer.js",
        layout="stream",
    )

    def create_pool(self, ctx: Any) -> Any:
        from octowright_terminal.pool import TerminalPool

        return TerminalPool(ctx)

    def create_scenario_adapter(self, pool: Any) -> Any:
        from octowright_terminal.scenario import TerminalScenarioAdapter

        return TerminalScenarioAdapter(pool)

    def session_detail(self, session: Any) -> dict[str, Any]:
        """Terminal's own additions to the dashboard detail payload.

        Core merges these under the uniform ``_live_summary`` base (started_at,
        live, protected, event/console/download/page counts, log_path, ...) —
        see ``plugin_session_detail`` in
        ``octowright.http.routes._session_kinds``. A terminal has no
        page/console/download/video/trace artefacts, so the browser-only paths
        are reported explicitly as ``None`` rather than omitted: a dashboard
        summary stays uniform across kinds instead of branching on which keys
        are present. Field names are byte-identical to core's former
        ``_terminal_session_detail`` — the dashboard reads them, and a rename
        here would be a silent UI break rather than a refactor.
        """
        return {
            "connector_type": getattr(session, "connector_type", None),
            "video_path": None,
            "trace_path": None,
            "markdown_path": None,
            "websocket_path": None,
            "action_count": int(getattr(getattr(session, "recorder", None), "action_count", 0)),
        }


plugin = TerminalPlugin()
