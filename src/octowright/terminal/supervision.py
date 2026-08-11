# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pure supervision decisions for the terminal poll task.

Kept free of any uterm import so it is importable — and testable — on a core
install where the ``octowright[terminal]`` extra is absent. The engine wires
the result into its poll-task done-callback.
"""

from __future__ import annotations

import asyncio


def poll_done_reason(exc: BaseException | None) -> str | None:
    """The ``terminal_stop`` reason to record when the poll task finishes.

    The poll loop normally ends one of two ways: it returns (EOF already
    recorded by the loop) or it is cancelled by ``stop()``. Either is a clean
    finish that needs no extra stop record. Any OTHER exception is an
    unexpected death (a ``poll_messages`` / recorder failure) that would
    otherwise vanish silently — surface it as an ``"error"`` stop so the
    recording and metrics reflect the failure.

    Returns ``None`` for a clean finish, or ``"error"`` for an unexpected one.
    """
    if exc is None or isinstance(exc, asyncio.CancelledError):
        return None
    return "error"
