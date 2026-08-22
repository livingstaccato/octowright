#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""ty gate with baseline-ratchet support.

NOTE ON PARITY WITH CI: `.github/workflows/ci.yml` runs `ty check src/octowright`
DIRECTLY -- no `--exit-zero`, no baseline -- so CI enforces ZERO diagnostics
regardless of what this file allows. Anything added to the baseline therefore
unblocks `make lint` while CI still fails on it. The baseline is kept empty for
that reason; treat a non-empty one as a deliberate, temporary divergence.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
from pathlib import Path

#: ty's ``--output-format concise`` shape: ``path:line:col: severity[rule] message``.
#: Anchored on the ``:line:col: severity[`` run rather than a prefix, because the
#: line STARTS WITH THE PATH -- a ``startswith("error[")`` filter matched nothing,
#: so the gate collected no diagnostics and could not fail. It also must not claim
#: the trailing ``Found N diagnostics`` summary, which would never match a baseline
#: entry and so would fail the gate on every run instead.
_DIAGNOSTIC_RE = re.compile(r":\d+:\d+: (?:error|warn|info)\[")


def _load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("allow_diagnostics", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return set()
    return {item for item in items if isinstance(item, str)}


def _extract_diagnostics(lines: list[str]) -> set[str]:
    """Diagnostic lines from ty's concise output, excluding summary/noise."""
    return {stripped for line in lines if (stripped := line.strip()) and _DIAGNOSTIC_RE.search(stripped)}


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
    diagnostics = _extract_diagnostics(proc.stdout.splitlines())
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
