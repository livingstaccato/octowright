# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Detect when the shared Playwright driver (one node process per pool) has died.

The pool keeps a single ``async_playwright().start()`` driver shared by every
browser. If that node process dies — crash, OOM, killed daemon generation — its
stdio pipe closes and every subsequent driver call fails with a connection/pipe
error, which would otherwise brick the whole pool until a restart. ``pool.launch`` uses
``is_driver_dead_error`` to recognise that class of failure, discard the dead
driver, and rebuild it on retry.

The match is on error *text* (Playwright surfaces these as generic
``playwright._impl._errors.Error`` / ``ValueError``), kept deliberately narrow so
ordinary per-launch failures (bad URL, missing binary, navigation error) are NOT
treated as driver death.
"""

from __future__ import annotations

# Substrings that only appear when the driver connection/transport itself is gone,
# not when a single browser action fails. Lower-cased compare.
_DRIVER_DEAD_MARKERS = (
    "connection closed",
    "target page, context or browser has been closed",
    "i/o operation on closed file",
    "browser has been closed",
    "transport closed",
    "pipe closed",
)


def is_driver_dead_error(exc: BaseException) -> bool:
    """True when ``exc`` indicates the shared Playwright driver connection died
    (as opposed to an ordinary per-launch failure)."""
    text = str(exc).lower()
    return any(marker in text for marker in _DRIVER_DEAD_MARKERS)
