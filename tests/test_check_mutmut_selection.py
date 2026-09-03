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


def test_paths_are_emitted_posix_style() -> None:
    """Entries must use forward slashes on every platform.

    pyproject.toml stores POSIX paths, so a Windows ``str(Path)`` rendering
    (``tests\\test_macros.py``) matches nothing and every file reads as missing.
    That is exactly how this check first failed CI: the guard runs Linux-only
    under `make lint`, but the test suite that calls it runs on Windows too.

    This assertion cannot fail on a POSIX host — it is here for the platform
    where it can.
    """
    for entry in _load_guard().expected_selection():
        assert "\\" not in entry, entry
        assert entry.startswith("tests/"), entry


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


def _pytest_ini() -> dict:
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["tool"]["pytest"]["ini_options"]


def test_the_mutants_workdir_is_never_collected() -> None:
    """A leftover ``mutants/`` must not break an ordinary run from the repo root.

    mutmut copies the whole project — ``conftest.py`` included — into
    ``mutants/`` and leaves it behind. pytest then walks it as just another
    directory, and a bare ``pytest`` at the root dies during collection with
    two errors, not one skipped test: ``ImportPathMismatchError`` on the
    duplicated ``tests.conftest``, and, under ``-p no:randomly``, ``option
    names {'--randomly-seed'} already added`` when both copies of the root
    conftest register the stand-in. So running ``make mutmut`` once made bare
    ``pytest`` unusable until someone deleted the directory by hand — and it
    defeated the very stand-in that was added so ``-p no:randomly`` would work.

    ``make test`` passes ``tests/`` explicitly and so never noticed.

    Verified behaviorally before this was pinned: with the directory present,
    ``pytest --collect-only`` exits ``Interrupted: 2 errors during collection``
    and ``--ignore=mutants`` collects cleanly.
    """
    assert "mutants" in _pytest_ini()["norecursedirs"]


def test_overriding_norecursedirs_keeps_pytest_s_own_defaults() -> None:
    """``norecursedirs`` REPLACES the built-in list rather than extending it.

    Setting it to ``["mutants"]`` alone would start collecting ``build/``,
    ``dist/``, ``node_modules/`` and every dotted directory — a much larger
    problem than the one being fixed, and one that would show up as mystery
    collection errors rather than as an obviously wrong setting.
    """
    configured = set(_pytest_ini()["norecursedirs"])
    assert {"*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}"} <= configured


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
            assert path.relative_to(ROOT).as_posix() not in expected
