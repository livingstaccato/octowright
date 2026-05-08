# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Verify compatibility agent instruction files stay in sync."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "AGENTS.md"
COMPATIBILITY = ROOT / "CLAUDE.md"


def main() -> int:
    if not CANONICAL.exists():
        print("AGENTS.md is missing; it is the canonical agent instructions file.")
        return 1
    if not COMPATIBILITY.exists():
        print("CLAUDE.md is missing; keep it as a regular file copy of AGENTS.md for Claude Code.")
        return 1
    if CANONICAL.read_bytes() != COMPATIBILITY.read_bytes():
        print("AGENTS.md and CLAUDE.md differ. Update AGENTS.md, then copy it to CLAUDE.md.")
        return 1
    print("Agent instruction docs are in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
