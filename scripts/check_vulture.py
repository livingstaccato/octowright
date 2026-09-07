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

# Drop the ":<lineno>" so an edit ABOVE a baselined finding does not shift it
# into looking new. Proven, not theoretical: adding one line to web_parse.py
# turned all three of its baselined callbacks into "new findings" and failed
# the gate. check_xenon.py has stripped line numbers for exactly this reason
# since it was written; this pass shipped without it.
_LINENO_RE = re.compile(r"^(.*?\.py):\d+:")


def _normalize(line: str) -> str:
    return _LINENO_RE.sub(r"\1:", line.strip())


def _load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("allow_findings", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return set()
    return {_normalize(item) for item in items if isinstance(item, str)}


# Vulture scores an unused FUNCTION, method or class at 60% confidence, so the
# 80% gate below structurally cannot report one -- it sees unused imports (90%)
# and unreachable code (100%) and nothing else. A dead module-level function was
# added, committed and passed a green gate before anyone noticed by eye.
#
# Lowering the main gate to 60 outright is the wrong repair: it adds 101 findings
# on src/ alone, 55 of them "unused variable", which is vulture's noisiest
# category (loop names, unpacked tuples). A baseline that large dilutes the
# signal, which is the same failure in a different shape.
#
# So a SECOND pass runs at 60 over src/ only, keeping just the function/method
# findings -- the exact gap -- and ignoring decorators that register a callable
# without ever naming it (Click commands, MCP tools, fixtures), which are
# false positives by construction rather than dead code.
DEAD_CODE_CONFIDENCE = 60
# Scan tests as well as src, then report only src findings. Scanning src alone
# calls anything used exclusively by a test dead -- measured at 38 findings
# src-only against 9 with tests included, so 29 of them were that mistake.
# Reporting only src keeps the gate off test-local helpers, where the
# framework-callback noise lives.
DEAD_CODE_SCAN_PATHS = ("src/octowright", "tests")
DEAD_CODE_REPORT_PREFIX = "src/octowright"
DEAD_CODE_KINDS = ("unused function", "unused method", "unused class")
# Comma-separated globs, per vulture's --ignore-decorators.
REGISTERING_DECORATORS = "@*.command,@*.group,@*.tool,@*.fixture,@pytest.*"


def _vulture(cmd: list[str]) -> list[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # nosec B603
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _dead_callables() -> list[str]:
    """Findings from the low-confidence pass, narrowed to callables."""
    lines = _vulture(
        [
            "vulture",
            *DEAD_CODE_SCAN_PATHS,
            "--min-confidence",
            str(DEAD_CODE_CONFIDENCE),
            "--ignore-decorators",
            REGISTERING_DECORATORS,
        ]
    )
    return [
        line
        for line in lines
        if line.startswith(DEAD_CODE_REPORT_PREFIX) and any(kind in line for kind in DEAD_CODE_KINDS)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run vulture gate with baseline ratchet support.")
    parser.add_argument("--baseline", type=Path, default=Path(".ci/vulture-baseline.json"))
    parser.add_argument("--min-confidence", type=int, default=80)
    parser.add_argument("--paths", nargs="+", default=["src/octowright", "tests"])
    parser.add_argument(
        "--skip-dead-callables",
        action="store_true",
        help="Run only the high-confidence pass (for bisecting a gate failure).",
    )
    args = parser.parse_args()

    lines = _vulture(["vulture", *args.paths, "--min-confidence", str(args.min_confidence)])
    if not args.skip_dead_callables:
        lines += _dead_callables()
    if not lines:
        print("vulture check passed: no findings.")
        return 0

    baseline = _load_baseline(args.baseline)
    current = {_normalize(line) for line in lines}
    new_findings = sorted(current - baseline)
    if new_findings:
        print("vulture check failed: new findings detected.")
        for line in new_findings:
            print(f"  {line}")
        return 1

    # A baseline is a ratchet, not a parking space: an entry whose finding is
    # gone means the code was fixed, and leaving it behind lets a LATER
    # regression in the same place land silently pre-approved. Only checked on
    # a full scan -- a caller narrowing --paths would otherwise see every
    # unscanned entry as stale.
    if not args.skip_dead_callables and set(args.paths) == set(parser.get_default("paths")):
        stale = sorted(baseline - current)
        if stale:
            print("vulture check failed: baseline entries no longer occur; delete them.")
            for line in stale:
                print(f"  {line}")
            return 1

    print("vulture check passed (baseline only): no new findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
