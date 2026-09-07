# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Verify CLAUDE.md is a symlink to AGENTS.md rather than a second copy.

AGENTS.md is the truth. CLAUDE.md exists only because Claude Code looks for
that name, and it used to be a byte-for-byte copy kept in step by this script
-- which meant every edit had to be made twice, and the guard's whole job was
catching the times someone forgot. A symlink cannot fall out of sync, so the
check becomes "is it still a symlink" instead of "do the bytes match".

Windows is the one wrinkle. Git only writes real symlinks there when
``core.symlinks`` is on (developer mode, or an elevated clone); otherwise it
materializes the link as a regular file whose entire contents are the target
path. That is a correct checkout of this repo, not a mistake, so it passes
with a note -- failing would make the repo unlintable on a stock Windows
clone. Anything else is a copy creeping back and is refused.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "AGENTS.md"
COMPATIBILITY = ROOT / "CLAUDE.md"


def main() -> int:
    if not CANONICAL.exists():
        print("AGENTS.md is missing; it is the canonical agent instructions file.")
        return 1
    if not COMPATIBILITY.exists() and not COMPATIBILITY.is_symlink():
        print("CLAUDE.md is missing; recreate it with: ln -s AGENTS.md CLAUDE.md")
        return 1

    if COMPATIBILITY.is_symlink():
        target = COMPATIBILITY.readlink()
        if target.name != CANONICAL.name or target.is_absolute():
            print(f"CLAUDE.md points at {target}; it must be a relative symlink to AGENTS.md.")
            return 1
        print("CLAUDE.md is a symlink to AGENTS.md.")
        return 0

    # A checkout without symlink support (see the module docstring).
    if COMPATIBILITY.read_text(encoding="utf-8").strip() == CANONICAL.name:
        print("CLAUDE.md is a git symlink materialized as text (core.symlinks off); that is fine.")
        return 0

    print("CLAUDE.md is a regular file. AGENTS.md is the truth; replace it with: ln -s AGENTS.md CLAUDE.md")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
