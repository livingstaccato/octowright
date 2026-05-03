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
    items = data.get("allow_findings", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return set()
    return {item for item in items if isinstance(item, str)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run vulture gate with baseline ratchet support.")
    parser.add_argument("--baseline", type=Path, default=Path(".ci/vulture-baseline.json"))
    parser.add_argument("--min-confidence", type=int, default=80)
    parser.add_argument("--paths", nargs="+", default=["src/octowright", "tests"])
    args = parser.parse_args()

    cmd = ["vulture", *args.paths, "--min-confidence", str(args.min_confidence)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        print("vulture check passed: no findings.")
        return 0

    baseline = _load_baseline(args.baseline)
    new_findings = sorted(set(lines) - baseline)
    if new_findings:
        print("vulture check failed: new findings detected.")
        for line in new_findings:
            print(f"  {line}")
        return 1

    print("vulture check passed (baseline only): no new findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
