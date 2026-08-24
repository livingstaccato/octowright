# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright_terminal.errors import ProtectedTerminalCloseError, TerminalPoolUnavailableError


def test_protected_terminal_close_error_is_value_error() -> None:
    err = ProtectedTerminalCloseError("nope")
    assert isinstance(err, ValueError)
    assert "nope" in str(err)


def test_terminal_pool_unavailable_error_is_runtime_error() -> None:
    # RuntimeError (not AssertionError) so the guard survives `python -O`, which
    # strips asserts, and so callers can catch it explicitly.
    err = TerminalPoolUnavailableError("no pool")
    assert isinstance(err, RuntimeError)
    assert "no pool" in str(err)
