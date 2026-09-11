# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Fail when an emitted OTel metric or MCP notification isn't documented.

Telemetry and the proactive notification taxonomy are part of octowright's public
contract — an operator graphs the metrics, the LLM reacts to the notifications. A
new ``counter(...)`` / ``histogram(...)`` or ``notifications/octowright/*`` method
that ships without a doc entry is invisible until someone trips over it. This is a
baseline-style ratchet (like agent-docs-sync): every instrument name and
notification method found in ``src`` must appear in one of the ``DOCS`` files.

Two files rather than one because the span/metric tables moved out of the root
``AGENTS.md`` — they are reference material an agent needs only when it touches
telemetry, and leaving them resident cost every session ~4k tokens to answer a
question most sessions never ask. The guard's contract is unchanged ("documented
somewhere an agent will read"), so it reads the concatenation: ``AGENTS.md`` still
names the four notification methods inline, and ``docs/telemetry.md`` holds the
tables. Checking only the root file would fail the moment reference prose is
filed where it belongs; checking only ``docs/`` would let the root file's own
summary drift unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "octowright"
DOCS = (ROOT / "AGENTS.md", ROOT / "docs" / "telemetry.md")

# Instrument names are quoted literals passed to counter()/histogram()/gauge();
# the octowright_* convention ends in _total (counter), _seconds (histogram),
# _bytes (the RSS gauge-as-histogram), or _depth (the operation-gate queue
# depth gauge).
_METRIC_RE = re.compile(r'"(octowright_[a-z_]+_(?:total|seconds|bytes|depth))"')
_NOTIF_RE = re.compile(r"notifications/octowright/([a-z_]+)")


def _scan(pattern: re.Pattern[str]) -> set[str]:
    names: set[str] = set()
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            names.add(match.group(1))
    return names


def metric_names() -> set[str]:
    """OTel metric instrument names emitted anywhere in src/octowright."""
    return _scan(_METRIC_RE)


def notification_methods() -> set[str]:
    """MCP ``notifications/octowright/<method>`` names emitted anywhere in src."""
    return _scan(_NOTIF_RE)


def doc_text() -> str:
    """The concatenated text of every file this guard accepts as documentation."""
    return "\n".join(path.read_text(encoding="utf-8") for path in DOCS)


def undocumented(doc_text: str) -> tuple[list[str], list[str]]:
    """Return (missing_metrics, missing_notifications) given the documentation text."""
    missing_metrics = sorted(m for m in metric_names() if m not in doc_text)
    missing_notifs = sorted(n for n in notification_methods() if f"notifications/octowright/{n}" not in doc_text)
    return missing_metrics, missing_notifs


def main() -> int:
    missing_metrics, missing_notifs = undocumented(doc_text())
    if missing_metrics or missing_notifs:
        names = " or ".join(path.relative_to(ROOT).as_posix() for path in DOCS)
        print(f"Telemetry/notification docs out of sync with code — add to {names}:")
        for name in missing_metrics:
            print(f"  metric not documented: {name}")
        for name in missing_notifs:
            print(f"  notification not documented: notifications/octowright/{name}")
        return 1
    print(
        f"OK: all {len(metric_names())} metrics + {len(notification_methods())} notifications "
        "are documented in AGENTS.md / docs/telemetry.md"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
