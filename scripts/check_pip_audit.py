# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""pip-audit gate with a tracked allow-list.

Reads `.ci/pip-audit-allow.txt` for vuln IDs to ignore (with justifications
captured in comments above each entry) and invokes pip-audit. Exits non-zero
when an unallowed vulnerability is reported.
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404
import sys
from pathlib import Path


def _load_allow(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not re.match(r"^[A-Z]+-[0-9A-Za-z-]+$", stripped):
            print(f"check_pip_audit: ignoring malformed allow entry {stripped!r}", file=sys.stderr)
            continue
        out.append(stripped)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pip-audit with tracked ignore list.")
    parser.add_argument("--allow", type=Path, default=Path(".ci/pip-audit-allow.txt"))
    parser.add_argument("--strict", action="store_true", help="fail on warnings as well as findings")
    args = parser.parse_args()

    cmd = ["pip-audit", "--strict"] if args.strict else ["pip-audit"]
    for vuln_id in _load_allow(args.allow):
        cmd += ["--ignore-vuln", vuln_id]

    proc = subprocess.run(cmd, check=False)  # nosec B603 B607
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
