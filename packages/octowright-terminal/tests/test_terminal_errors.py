# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright_terminal.errors import ProtectedTerminalCloseError, TerminalPoolUnavailableError

from octowright.plugins.errors import ProtectedSessionCloseError


def test_protected_terminal_close_error_is_cores_contract_type() -> None:
    # The contract type, not merely a similarly-named one: core's
    # `_maybe_close_plugin` catches ProtectedSessionCloseError and nothing
    # else, so anything outside this hierarchy escapes the route as a 500.
    err = ProtectedTerminalCloseError("nope")
    assert isinstance(err, ProtectedSessionCloseError)
    assert "nope" in str(err)


def test_protected_terminal_close_error_is_still_a_value_error() -> None:
    # Kept in the MRO on purpose: `terminal_launch`'s broad `except ValueError`
    # and any external caller catching it as one must keep working.
    assert isinstance(ProtectedTerminalCloseError("nope"), ValueError)


def test_terminal_pool_unavailable_error_is_runtime_error() -> None:
    # RuntimeError (not AssertionError) so the guard survives `python -O`, which
    # strips asserts, and so callers can catch it explicitly.
    err = TerminalPoolUnavailableError("no pool")
    assert isinstance(err, RuntimeError)
    assert "no pool" in str(err)
