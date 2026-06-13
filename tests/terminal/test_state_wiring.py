# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations


def test_state_terminal_pool_matches_availability() -> None:
    from octowright.server import _state
    from octowright.terminal import is_available

    if is_available():
        from octowright.terminal.pool import TerminalPool

        assert isinstance(_state.terminal_pool, TerminalPool)
    else:
        assert _state.terminal_pool is None
