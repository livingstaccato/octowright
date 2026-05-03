#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
from pathlib import Path


def _load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("allow_diagnostics", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return set()
    return {item for item in items if isinstance(item, str)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ty gate with baseline ratchet support.")
    parser.add_argument("--baseline", type=Path, default=Path(".ci/ty-baseline.json"))
    parser.add_argument("--paths", nargs="+", default=["src/octowright"])
    args = parser.parse_args()

    cmd = [
        "ty",
        "check",
        "--output-format",
        "concise",
        "--exit-zero",
        *args.paths,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    diagnostics = {
        line for line in lines if line.startswith("error[") or line.startswith("warn[") or line.startswith("info[")
    }
    if not diagnostics:
        print("ty check passed: no diagnostics.")
        return 0

    baseline = _load_baseline(args.baseline)
    new_diagnostics = sorted(diagnostics - baseline)
    if new_diagnostics:
        print("ty check failed: new diagnostics detected.")
        for line in new_diagnostics:
            print(f"  {line}")
        return 1

    print("ty check passed (baseline only): no new diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
