# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Fail when mutmut's test selection has drifted from the tests that exist.

``[tool.mutmut]`` mutates whole modules (``source_paths``) but runs only the
files named in ``pytest_add_cli_args_test_selection``. Those two lists are
maintained independently, and on 2026-08-27 they had drifted far enough to make
the results actively misleading: the selection named 32 of 382 test files, and
**187 test files that touch a mutated module never ran at all**.

The cost is not a slower run, it is a wrong answer that looks like a real one.
mutmut scores a mutant as *survived* when the tests it ran did not fail, so a
mutant whose killing test was simply never executed is reported identically to
a genuine assertion gap. Four separate security-looking findings in that run --
a defeated CR/LF YAML-injection guard, a credential leaking into a macro URL,
ten credential-linter bypasses, and a stripped redaction path -- all turned out
to be tested by files the selection omitted. Chasing them cost a full triage
pass across 831 survivors.

So the guard is not about tidiness. It exists because a mutation score nobody
can trust is worse than no mutation score: it spends real review time on
phantom findings while hiding the handful of real ones in the noise.

The rule enforced here: every *fast* test file that imports a mutated package
must appear in the selection. Slow suites are excluded deliberately -- a
per-mutant run that launches real browsers would take the nightly past its
four-hour ceiling -- and that exclusion is by marker, so it stays correct as
suites are added.

**The computed set is a floor, not an exact match**, and the difference is
load-bearing. This check detects membership by *import*, while mutmut
associates tests to functions by *coverage* -- so a test that reaches mutated
code transitively is invisible here. ``tests/macro_lint/test_cli_export_semantic.py``
is exactly that: it imports ``octowright.artifacts.script_export`` and touches
``macros/artifacts.py`` only through the call chain, and regenerating the
selection from this heuristic alone silently dropped it. Extra entries are
therefore always allowed; only *missing* files and entries naming a path that
no longer exists are errors.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: A test file is in scope when it imports one of the mutated packages.
IMPORTS_MUTATED = re.compile(
    r"from octowright\.(macros|personas|scenarios)"
    r"|import octowright\.(macros|personas|scenarios)"
    r"|from octowright import.*(macros|personas|scenarios)"
)

#: ...and out of scope when it carries a marker for a suite mutmut must not run
#: per mutant. Matched against file text rather than a marker API so this stays
#: a cheap static check with no collection side effects.
SLOW_MARKERS = re.compile(r"live_browser|integration_local|memory_isolated|engine_matrix")


def expected_selection() -> list[str]:
    """Every fast test file that imports a mutated package.

    Paths are emitted POSIX-style. ``str(Path)`` renders backslashes on
    Windows, which would not match the forward slashes pyproject.toml stores,
    so every entry would read as missing and the check would fail on Windows
    for a reason that has nothing to do with the selection.
    """
    out: list[str] = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if IMPORTS_MUTATED.search(text) and not SLOW_MARKERS.search(text):
            out.append(path.relative_to(ROOT).as_posix())
    return out


def configured_selection() -> list[str]:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(cfg["tool"]["mutmut"]["pytest_add_cli_args_test_selection"])


def main() -> int:
    configured = configured_selection()
    expected = expected_selection()

    missing = sorted(set(expected) - set(configured))
    stale = sorted(p for p in configured if not (ROOT / p).exists())

    if not missing and not stale:
        print(f"mutmut test selection is in sync ({len(configured)} files).")
        return 0

    if missing:
        print(
            f"{len(missing)} test file(s) import a mutated package but are missing from\n"
            "[tool.mutmut] pytest_add_cli_args_test_selection. Mutants they would kill\n"
            "will be reported as survivors:",
            file=sys.stderr,
        )
        for path in missing:
            print(f"    {path}", file=sys.stderr)
    if stale:
        print(f"\n{len(stale)} selected file(s) no longer exist:", file=sys.stderr)
        for path in stale:
            print(f"    {path}", file=sys.stderr)
    print("\nAdd or remove the paths above in pyproject.toml.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
