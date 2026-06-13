# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.terminal.errors import ProtectedTerminalCloseError


def test_protected_terminal_close_error_is_value_error() -> None:
    err = ProtectedTerminalCloseError("nope")
    assert isinstance(err, ValueError)
    assert "nope" in str(err)
