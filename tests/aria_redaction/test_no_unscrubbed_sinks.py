# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Fail if a new accessibility-snapshot sink bypasses the credential scrubber.

The leak this guards was not one bug in one place: seven call sites each
rendered a filled password box into cleartext, and any eighth would do it
again. Routing every sink through ``aria_redaction.aria_snapshot`` is only
durable if adding a raw call is loud.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "octowright"

# The scrubber itself is the one place allowed to call Playwright directly.
ALLOWED = {SRC / "session" / "aria_redaction.py"}


def _raw_aria_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "aria_snapshot"
    ]


def test_every_aria_snapshot_goes_through_the_scrubber() -> None:
    offenders = {
        str(path.relative_to(SRC)): lines
        for path in sorted(SRC.rglob("*.py"))
        if path not in ALLOWED and (lines := _raw_aria_calls(path))
    }
    assert not offenders, (
        "raw locator.aria_snapshot() calls leak credential values into the "
        "accessibility tree; call octowright.session.aria_redaction.aria_snapshot "
        f"instead: {offenders}"
    )


def test_the_guard_can_actually_see_a_raw_call(tmp_path: Path) -> None:
    """A scanner that never matches would pass the test above forever."""
    sample = tmp_path / "sink.py"
    sample.write_text("async def f(loc):\n    return await loc.aria_snapshot()\n")
    assert _raw_aria_calls(sample) == [2]
