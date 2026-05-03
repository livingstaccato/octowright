#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
from pathlib import Path


def _load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("allow_violations", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return set()
    return {_normalize_violation(item) for item in items if isinstance(item, str)}


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _normalize_violation(line: str) -> str:
    text = _strip_ansi(line).strip()
    if text.startswith("ERROR:xenon:"):
        text = text.removeprefix("ERROR:xenon:").strip()
    # act/log wrappers can inject extra spacing; compare canonicalized text.
    return " ".join(text.split())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run xenon gate with baseline ratchet support.")
    parser.add_argument("--baseline", type=Path, default=Path(".ci/xenon-baseline.json"))
    parser.add_argument("--paths", nargs="+", default=["src/octowright"])
    parser.add_argument("--max-absolute", default="B")
    parser.add_argument("--max-modules", default="B")
    parser.add_argument("--max-average", default="A")
    args = parser.parse_args()

    cmd = [
        "xenon",
        "--max-absolute",
        args.max_absolute,
        "--max-modules",
        args.max_modules,
        "--max-average",
        args.max_average,
        *args.paths,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
    if proc.returncode == 0:
        print("xenon check passed: no violations.")
        return 0

    baseline = _load_baseline(args.baseline)
    merged = "\n".join([proc.stdout, proc.stderr])
    plain = _strip_ansi(merged)
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    raw_violations = [line for line in lines if "ERROR:xenon:" in line]
    violations = [_normalize_violation(line) for line in raw_violations]
    new_violations = [line for line in violations if line not in baseline]
    if new_violations:
        print("xenon check failed: new complexity violations detected.")
        for line in new_violations:
            print(f"  {line}")
        return 1

    print("xenon check passed (baseline only): no new complexity violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
