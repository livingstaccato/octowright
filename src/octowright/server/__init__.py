# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP server entrypoint.

The shared FastMCP instance and live pools live in ``_state``. Each domain
submodule (`browser`, `personas`, `macros`, `goldens`, `scenarios`) registers
its tools against that shared instance at import time, so importing this
package is enough to make every tool callable.

`registered_tool_names()` and `recordings_dir()` are kept here at the top
level for `cli.py selftest` and external introspection.
"""

from __future__ import annotations

from pathlib import Path

from ..defaults import RECORDINGS_DIR

# Submodule imports trigger @mcp.tool registration via decorator side effects.
# Order does not matter; F401 ignored intentionally.
from . import browser as _browser  # noqa: F401
from . import goldens as _goldens  # noqa: F401
from . import macros as _macros  # noqa: F401
from . import meta as _meta  # noqa: F401
from . import personas as _personas  # noqa: F401
from . import scenarios as _scenarios  # noqa: F401
from ._state import log, mcp, pool, scenario_pool

# Re-export every tool function at the package level for direct test access
# (e.g. `from octowright import server; server.browser_open_trace(...)`).
from .browser import (
    browser_click,
    browser_click_by,
    browser_close,
    browser_close_all,
    browser_console_messages,
    browser_downloads,
    browser_evaluate,
    browser_expect_js,
    browser_expect_selector,
    browser_expect_text,
    browser_expect_url,
    browser_export_script,
    browser_fill,
    browser_fill_by,
    browser_get_text_by,
    browser_launch,
    browser_list,
    browser_list_frames,
    browser_mock_route,
    browser_navigate,
    browser_open_trace,
    browser_press_key,
    browser_recording_path,
    browser_reset_frame,
    browser_screenshot,
    browser_set_dialog_policy,
    browser_set_input_files,
    browser_snapshot,
    browser_spawn_roster,
    browser_suggest_for_url,
    browser_switch_frame,
    browser_tail_recording,
    browser_type,
    browser_unmock_route,
    browser_wait_for,
    browser_wait_for_download,
    page_close,
    page_list,
    page_switch,
)
from .goldens import golden_assert, golden_delete, golden_list, golden_save
from .macros import (
    macro_delete,
    macro_lint,
    macro_list,
    macro_run,
    macro_run_sequence,
    macro_save,
    profile_cleanup,
    recordings_cleanup,
    run_test_suite,
)
from .meta import octowright_check_takeover, octowright_dashboard_url, octowright_status
from .personas import (
    migrate_profiles,
    persona_create,
    persona_credentials_check,
    persona_delete,
    persona_get,
    persona_list,
    profile_delete,
    profile_list,
)
from .scenarios import (
    scenario_list,
    scenario_participants,
    scenario_plan,
    scenario_run_as_test,
    scenario_run_macro,
    scenario_start,
    scenario_status,
    scenario_stop,
    scenario_tail,
)


def registered_tool_names() -> list[str]:
    """Used by `cli.py selftest` to verify registration without a client."""
    return sorted(t.name for t in mcp._tool_manager.list_tools())


def recordings_dir() -> Path:
    return RECORDINGS_DIR


__all__ = [
    "browser_click",
    "browser_click_by",
    "browser_close",
    "browser_close_all",
    "browser_console_messages",
    "browser_downloads",
    "browser_evaluate",
    "browser_expect_js",
    "browser_expect_selector",
    "browser_expect_text",
    "browser_expect_url",
    "browser_export_script",
    "browser_fill",
    "browser_fill_by",
    "browser_get_text_by",
    "browser_launch",
    "browser_list",
    "browser_list_frames",
    "browser_mock_route",
    "browser_navigate",
    "browser_open_trace",
    "browser_press_key",
    "browser_recording_path",
    "browser_reset_frame",
    "browser_screenshot",
    "browser_set_dialog_policy",
    "browser_set_input_files",
    "browser_snapshot",
    "browser_spawn_roster",
    "browser_suggest_for_url",
    "browser_switch_frame",
    "browser_tail_recording",
    "browser_type",
    "browser_unmock_route",
    "browser_wait_for",
    "browser_wait_for_download",
    "golden_assert",
    "golden_delete",
    "golden_list",
    "golden_save",
    "log",
    "macro_delete",
    "macro_lint",
    "macro_list",
    "macro_run",
    "macro_run_sequence",
    "macro_save",
    "mcp",
    "migrate_profiles",
    "octowright_check_takeover",
    "octowright_dashboard_url",
    "octowright_status",
    "page_close",
    "page_list",
    "page_switch",
    "persona_create",
    "persona_credentials_check",
    "persona_delete",
    "persona_get",
    "persona_list",
    "pool",
    "profile_cleanup",
    "profile_delete",
    "profile_list",
    "recordings_cleanup",
    "recordings_dir",
    "registered_tool_names",
    "run_test_suite",
    "scenario_list",
    "scenario_participants",
    "scenario_plan",
    "scenario_pool",
    "scenario_run_as_test",
    "scenario_run_macro",
    "scenario_start",
    "scenario_status",
    "scenario_stop",
    "scenario_tail",
]
