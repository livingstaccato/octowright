# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""In-process terminal sessions (PTY / SSH) for Octowright.

All `provide-uterm` usage is quarantined inside this package: the rest of
Octowright sees only Octowright's generic session shape and `{ts, action, …}`
JSONL recordings. See docs/superpowers/specs/2026-06-12-terminal-sessions-design.md.
"""

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
