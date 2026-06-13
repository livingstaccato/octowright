# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Detect whether the optional ``octowright[terminal]`` extra is installed."""

from __future__ import annotations


def is_available() -> bool:
    """Return True iff the optional ``octowright[terminal]`` extra is installed.

    Import-light on purpose: imports only the uterm connector entry module —
    never ``octowright.terminal.engine``/``pool`` (which import uterm at module
    top) — so it is safe to call on a core install where the extra is absent.
    """
    try:
        import provide.uterm.server.connectors  # noqa: F401
    except ImportError:
        return False
    return True
