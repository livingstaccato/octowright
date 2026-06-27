# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP tool surface for terminal sessions (registered only when available)."""

from __future__ import annotations

from octowright.server.terminal.lifecycle import (
    terminal_close,
    terminal_launch,
    terminal_list,
    terminal_read,
    terminal_send_input,
    terminal_snapshot,
    terminal_wait_for,
)

__all__ = [
    "terminal_close",
    "terminal_launch",
    "terminal_list",
    "terminal_read",
    "terminal_send_input",
    "terminal_snapshot",
    "terminal_wait_for",
]
