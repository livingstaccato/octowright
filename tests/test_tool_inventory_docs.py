# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The tool-inventory guard, and why counting by hand does not survive.

``docs/architecture/mcp-tool-inventory.md`` calls itself the full per-tool
inventory and tells the reader to trust ``octowright selftest`` over it. That
disclaimer is honest and it is also the problem: the doc had drifted by two
tools (``browser_a11y_dragdrop`` and ``macro_artifact_delete`` were both
registered and both absent from the all-only list), so its all-only section
said 27 where the registry said 29. README.md had inherited the same arithmetic
and advertised a 131-tool surface as ``129`` two lines below saying 131.

Nothing could have caught either: every count in both files is typed by hand.
This guard measures the live registry instead, in the same spirit as
``check_telemetry_docs.py`` — a new tool that ships undocumented is a contract
change nobody announced.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "_check_tool_inventory_docs", ROOT / "scripts" / "check_tool_inventory_docs.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load_guard()


def test_the_committed_docs_satisfy_the_guard(guard) -> None:
    """The repository must pass its own check."""
    assert guard.main() == 0


def test_the_live_surface_is_measured_without_ambient_plugins(guard) -> None:
    """A developer whose own ``plugins.yaml`` enables terminal must still pass.

    ``enabled_names`` treats an empty ``OCTOWRIGHT_PLUGINS`` as unset and falls
    through to the user's config file, so measuring in-process would count 138
    tools on the maintainer's machine and 131 in CI — the exact ambient-config
    split that made an assertion in ``ci/run_terminal_plugin_tests.sh`` fail
    locally while passing in CI. The measurement therefore runs in a child with
    its config dirs redirected.
    """
    names = guard.core_tool_names()
    assert "browser_launch" in names
    assert not any(name.startswith("terminal_") for name in names)


def test_a_tool_missing_from_the_inventory_is_reported(guard) -> None:
    """The drift that actually happened: a registered tool nobody listed."""
    text = (ROOT / "docs" / "architecture" / "mcp-tool-inventory.md").read_text(encoding="utf-8")
    mangled = text.replace("`browser_a11y_dragdrop`, ", "")
    problems = guard.problems(mangled, (ROOT / "README.md").read_text(encoding="utf-8"))
    assert any("browser_a11y_dragdrop" in p for p in problems), problems


def test_a_stale_count_is_reported_even_when_the_list_is_right(guard) -> None:
    """The header count and the list are two hand-written numbers, not one.

    The doc shipped a correct 27-name list under a header that said 27 while
    the registry held 29 — so a guard that only compared lists would have
    passed it. Both are checked.
    """
    text = (ROOT / "docs" / "architecture" / "mcp-tool-inventory.md").read_text(encoding="utf-8")
    mangled = text.replace("### `core` (24)", "### `core` (23)")
    problems = guard.problems(mangled, (ROOT / "README.md").read_text(encoding="utf-8"))
    assert any("core" in p and "23" in p for p in problems), problems


def test_every_live_doc_claiming_a_core_install_total_is_checked(guard) -> None:
    """The prose total drifted furthest where nobody was looking.

    README said 131 and ``docs/getting-started.md`` said **126** — five
    releases of tools behind — because each file carries its own hand-typed
    copy of the same sentence. Scanning for the phrase means a new doc that
    repeats it is covered the day it is written, rather than the day someone
    remembers to add it to a list here.
    """
    scanned = {p.relative_to(ROOT).as_posix() for p in guard.docs_claiming_a_total()}
    assert "README.md" in scanned
    assert "AGENTS.md" in scanned
    assert "docs/getting-started.md" in scanned


def test_the_changelog_and_plan_records_are_not_rewritten(guard) -> None:
    """History says what was true then, and must not be dragged forward.

    CHANGELOG.md records a 129-tool surface for the release that had one, and
    the plan documents quote the counts they were written against. Checking
    them would demand edits that falsify the record.
    """
    scanned = {p.relative_to(ROOT).as_posix() for p in guard.docs_claiming_a_total()}
    assert "CHANGELOG.md" not in scanned
    assert not any(name.startswith("docs/superpowers/") for name in scanned), scanned


def test_a_stale_prose_total_is_reported(guard) -> None:
    """The getting-started drift, reproduced."""
    problems = guard.problems(
        (ROOT / "docs" / "architecture" / "mcp-tool-inventory.md").read_text(encoding="utf-8"),
        (ROOT / "README.md").read_text(encoding="utf-8"),
        prose_totals={ROOT / "docs" / "getting-started.md": 126},
    )
    assert any("getting-started" in p and "126" in p for p in problems), problems


def test_the_diagram_carries_the_same_counts_and_is_checked(guard) -> None:
    """``mcp-tool-surface.puml`` hand-types every count the inventory does.

    It had drifted furthest of all — a title reading 126 and an ``all-only
    (27)`` package — and unlike the markdown it also ships as a committed SVG,
    so a wrong number is rendered into an image a reader trusts more than
    prose. Its counts are checked from the same measurement.
    """
    text = (ROOT / "docs" / "architecture" / "mcp-tool-surface.puml").read_text(encoding="utf-8")
    assert guard.diagram_problems(text, guard.core_surface()) == []

    stale = text.replace('package "core (24)"', 'package "core (22)"')
    assert any("core" in p and "22" in p for p in guard.diagram_problems(stale, guard.core_surface()))

    stale_title = text.replace("(133 tools total", "(126 tools total")
    assert any("126" in p for p in guard.diagram_problems(stale_title, guard.core_surface()))


def test_a_stale_readme_total_is_reported(guard) -> None:
    """README's own total drifted while the sentence above it stayed right."""
    text = (ROOT / "docs" / "architecture" / "mcp-tool-inventory.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    mangled = readme.replace("every core-install tool registers. | 133 |", "every core-install tool registers. | 129 |")
    problems = guard.problems(text, mangled)
    assert any("README" in p and "129" in p for p in problems), problems


def test_a_nested_worktree_is_not_scanned(guard) -> None:
    """A git worktree under ``.claude/worktrees/`` is a second copy of this repo.

    Every exclusion here was originally a prefix match on the repo-relative
    path, so ``CHANGELOG.md`` and ``docs/superpowers/`` were skipped at the
    root and scanned again one directory down. The moment an agent worktree
    existed, `make lint` failed with
    ``.claude/worktrees/agent-.../CHANGELOG.md: says 129 tools`` -- a real
    historical record, in a checkout nobody was editing, reported as drift.

    Skipping by path SEGMENT rather than prefix is what makes that structural:
    a dot-prefixed directory (``.claude``, ``.venv``, ``.git``) is never a
    source of canonical documentation, wherever it sits in the tree.
    """
    scanned = {p.relative_to(ROOT).as_posix() for p in guard.docs_claiming_a_total()}
    assert not any(part.startswith(".") for name in scanned for part in name.split("/")), scanned
    assert not any("/CHANGELOG.md" in name for name in scanned), scanned


def test_the_scan_skips_dot_directories_at_any_depth(guard) -> None:
    """Unit-level companion, so the rule holds with no worktree on disk.

    The test above only proves anything while a worktree happens to exist.
    """
    assert guard._is_skipped_doc_path("CHANGELOG.md")
    assert guard._is_skipped_doc_path("docs/superpowers/plans/x.md")
    assert guard._is_skipped_doc_path(".claude/worktrees/agent-1/CHANGELOG.md")
    assert guard._is_skipped_doc_path(".claude/worktrees/agent-1/README.md")
    assert guard._is_skipped_doc_path("mutants/README.md")
    assert guard._is_skipped_doc_path("packages/octowright-frontend/node_modules/x/README.md")
    assert not guard._is_skipped_doc_path("README.md")
    assert not guard._is_skipped_doc_path("docs/getting-started.md")
