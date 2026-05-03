#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Coverage ratchet gate.")
    parser.add_argument("--coverage-json", type=Path, default=Path("dist/coverage.json"))
    parser.add_argument("--baseline", type=Path, default=Path(".ci/coverage-baseline.json"))
    args = parser.parse_args()

    cov = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    base = json.loads(args.baseline.read_text(encoding="utf-8"))
    current = float(cov["totals"]["percent_covered"])
    required = float(base["min_total_coverage"])
    if current + 1e-9 < required:
        print(f"coverage gate failed: {current:.2f}% < baseline {required:.2f}%")
        return 1
    print(f"coverage gate passed: {current:.2f}% >= baseline {required:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
