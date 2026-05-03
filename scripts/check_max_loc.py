# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

MAX_LOC = 500
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _line_count(path: Path) -> int:
    return sum(1 for _ in path.open("r", encoding="utf-8"))


def main() -> int:
    offenders: list[tuple[Path, int]] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        count = _line_count(path)
        if count > MAX_LOC:
            offenders.append((path, count))

    if offenders:
        print(f"Files over {MAX_LOC} LOC:")
        for path, count in offenders:
            rel = path.relative_to(ROOT)
            print(f"  - {rel}: {count}")
        return 1
    print(f"OK: all Python files are <= {MAX_LOC} LOC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
