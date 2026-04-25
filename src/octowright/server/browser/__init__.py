# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Browser-domain MCP tools.

Each submodule registers its tools against the shared FastMCP instance from
`octowright.server._state` at import time. Importing this package is enough
to make every browser_* tool callable.
"""

from __future__ import annotations

from . import input as _input  # noqa: F401
from . import inspect as _inspect  # noqa: F401
from . import lifecycle as _lifecycle  # noqa: F401
from . import media as _media  # noqa: F401
from . import network as _network  # noqa: F401
from . import views as _views  # noqa: F401

# Re-export selected tool functions for direct test access (`from octowright import server;
# server.browser_open_trace(...)`).
from .input import (
    browser_click,
    browser_click_by,
    browser_fill,
    browser_fill_by,
    browser_get_text_by,
    browser_press_key,
    browser_set_input_files,
    browser_type,
)
from .inspect import (
    browser_console_messages,
    browser_evaluate,
    browser_expect_js,
    browser_expect_selector,
    browser_expect_text,
    browser_expect_url,
    browser_export_script,
    browser_recording_path,
    browser_screenshot,
    browser_snapshot,
    browser_tail_recording,
    browser_wait_for,
)
from .lifecycle import (
    browser_close,
    browser_close_all,
    browser_launch,
    browser_list,
    browser_navigate,
    browser_spawn_roster,
    browser_suggest_for_url,
)
from .media import browser_extract_frames, browser_open_trace, browser_video_path
from .network import browser_mock_route, browser_set_dialog_policy, browser_unmock_route
from .views import (
    browser_downloads,
    browser_list_frames,
    browser_reset_frame,
    browser_switch_frame,
    browser_wait_for_download,
    page_close,
    page_list,
    page_switch,
)

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
    "browser_extract_frames",
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
    "browser_video_path",
    "browser_wait_for",
    "browser_wait_for_download",
    "page_close",
    "page_list",
    "page_switch",
]
