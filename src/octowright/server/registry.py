# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

from octowright.defaults import RECORDINGS_DIR
from octowright.server._state import mcp


def registered_tool_names() -> list[str]:
    return sorted(t.name for t in mcp._tool_manager.list_tools())


def recordings_dir() -> Path:
    return RECORDINGS_DIR
