# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The mutmut-selection guard is a floor check, never an equality check.

This distinction has already caused one real defect. The guard detects a test
file by its *imports*, but mutmut associates tests to functions by *coverage*,
so a test that reaches mutated code transitively is invisible to the heuristic.
Regenerating the selection from the heuristic alone silently dropped
``tests/macro_lint/test_cli_export_semantic.py`` -- which imports
``octowright.artifacts.script_export`` and touches ``macros/artifacts.py`` only
through the call chain.

An equality check would make that data loss the *enforced* state: the guard
would demand removal of every deliberately-added entry it cannot infer. So
extra entries must always pass, and these tests pin that.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "_check_mutmut_selection", ROOT / "scripts" / "check_mutmut_selection.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_repository_selection_is_currently_in_sync() -> None:
    """The committed pyproject must satisfy its own guard."""
    assert _load_guard().main() == 0


def test_extra_entries_are_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file the heuristic cannot infer must not be reported as a problem.

    This is the transitive-coverage case. Demanding its removal would delete
    real mutation coverage and look like tidiness while doing it.
    """
    guard = _load_guard()
    monkeypatch.setattr(guard, "expected_selection", lambda: ["tests/test_macros.py"])
    monkeypatch.setattr(
        guard,
        "configured_selection",
        lambda: ["tests/test_macros.py", "tests/macro_lint/test_cli_export_semantic.py"],
    )
    assert guard.main() == 0


def test_a_missing_file_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case the guard exists for: a covering test that would never run."""
    guard = _load_guard()
    monkeypatch.setattr(guard, "expected_selection", lambda: ["tests/test_macros.py"])
    monkeypatch.setattr(guard, "configured_selection", lambda: [])
    assert guard.main() == 1


def test_an_entry_naming_a_deleted_file_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A renamed or deleted test leaves a stale path that silently runs nothing."""
    guard = _load_guard()
    monkeypatch.setattr(guard, "expected_selection", lambda: [])
    monkeypatch.setattr(guard, "configured_selection", lambda: ["tests/test_does_not_exist.py"])
    assert guard.main() == 1


def test_slow_suites_are_excluded_by_marker_not_by_name() -> None:
    """Marker-based exclusion is what keeps the rule correct as suites are added.

    A per-mutant run that launches real browsers would blow the nightly's
    four-hour ceiling, so live suites must stay out — but by what they declare,
    not by a hand-listed set of filenames that drifts the same way the selection
    itself did.
    """
    guard = _load_guard()
    expected = set(guard.expected_selection())
    for path in (ROOT / "tests").rglob("test_*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "live_browser" in text or "memory_isolated" in text:
            assert str(path.relative_to(ROOT)) not in expected
