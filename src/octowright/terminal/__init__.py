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

from octowright.terminal.availability import is_available

__all__ = ["is_available"]
