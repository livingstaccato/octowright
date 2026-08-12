# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal poll-task supervision + disconnected-input signalling.

These test the uterm-FREE decision helpers, so they run on a core install
(where the octowright[terminal] extra — and thus the uterm-importing engine —
is absent). The engine wires them; the logic lives here so it is testable
without a live connector.
"""

from __future__ import annotations

import asyncio


def test_clean_poll_finish_records_no_extra_stop() -> None:
    from octowright.terminal.supervision import poll_done_reason

    # A normal return (EOF already recorded by the loop) needs no extra stop.
    assert poll_done_reason(None) is None


def test_cancelled_poll_is_not_an_error() -> None:
    from octowright.terminal.supervision import poll_done_reason

    # stop() cancels the poll task — that is the normal teardown, not a failure.
    assert poll_done_reason(asyncio.CancelledError()) is None


def test_unexpected_poll_exception_is_an_error_stop() -> None:
    from octowright.terminal.supervision import poll_done_reason

    # A poll/recorder exception would otherwise kill the task silently; it must
    # surface as an 'error' terminal_stop.
    assert poll_done_reason(RuntimeError("poll blew up")) == "error"
    assert poll_done_reason(OSError("connector gone")) == "error"


def test_terminal_disconnected_error_is_a_runtime_error() -> None:
    from octowright.terminal.errors import TerminalDisconnectedError

    # send_input raises it when the connector is gone; the tool maps it to
    # {"ok": False}. A distinct type lets callers catch delivery failure only.
    assert issubclass(TerminalDisconnectedError, RuntimeError)
