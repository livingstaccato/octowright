# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal poll-task supervision + disconnected-input signalling.

These cover the uterm-FREE decision helpers -- the engine wires them, but the
logic lives apart from it so it is testable without a live connector.

The file used to sit in core's ``tests/``, back when it could: the plugin was
a member of the ``dev`` dependency group, so ``octowright_terminal`` was
installed in every environment core's suite ran in. Task 12 moved the plugin
into its own ``terminal`` group that core's CI legs deliberately do NOT sync
(see the root ``pyproject.toml``), which would have left this file failing at
import in every core job. It tests the plugin's own module, so it belongs in
the plugin's own suite -- where the ``terminal`` marker also auto-applies.
"""

from __future__ import annotations

import asyncio

from octowright_terminal.errors import TerminalDisconnectedError
from octowright_terminal.supervision import poll_done_reason


def test_clean_poll_finish_records_no_extra_stop() -> None:
    # A normal return (EOF already recorded by the loop) needs no extra stop.
    assert poll_done_reason(None) is None


def test_cancelled_poll_is_not_an_error() -> None:
    # stop() cancels the poll task — that is the normal teardown, not a failure.
    assert poll_done_reason(asyncio.CancelledError()) is None


def test_unexpected_poll_exception_is_an_error_stop() -> None:
    # A poll/recorder exception would otherwise kill the task silently; it must
    # surface as an 'error' terminal_stop.
    assert poll_done_reason(RuntimeError("poll blew up")) == "error"
    assert poll_done_reason(OSError("connector gone")) == "error"


def test_terminal_disconnected_error_is_a_runtime_error() -> None:
    # send_input raises it when the connector is gone; the tool maps it to
    # {"ok": False}. A distinct type lets callers catch delivery failure only.
    assert issubclass(TerminalDisconnectedError, RuntimeError)
