# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The ty gate must actually be able to fail.

`scripts/check_ty.py` collected diagnostics with `line.startswith("error[")`,
but it runs ty with `--output-format concise`, whose lines start with the FILE
PATH:

    src/octowright/foo.py:6:12: error[unresolved-attribute] Object of type ...

So the filter matched nothing, the diagnostic set was always empty, and the
script printed "ty check passed: no diagnostics." and returned 0 no matter what
ty found. `make lint` advertised a type gate that could not fail, and the
154-entry baseline it ratchets against was never consulted.

Measured against real ty output before the fix: one file with one genuine
`unresolved-attribute` produced two output lines and zero matches.

The sibling ratchets do not share this: `check_vulture` treats every non-empty
line as a finding, and `check_xenon` matches `"ERROR:xenon:" in line` as a
substring rather than a prefix.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_ty.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_ty", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Verbatim ty --output-format concise output for one real diagnostic.
_CONCISE_ERROR = (
    "src/octowright/session/frames.py:34:24: error[call-non-callable] Object of type `Locator` is not callable"
)
_SUMMARY = "Found 1 diagnostic"


class TestDiagnosticExtraction:
    def test_a_concise_diagnostic_is_collected(self) -> None:
        """The regression itself: this line starts with a path, not `error[`."""
        assert _load()._extract_diagnostics([_CONCISE_ERROR]) == {_CONCISE_ERROR}

    @pytest.mark.parametrize("severity", ["error", "warn", "info"])
    def test_every_severity_is_collected(self, severity: str) -> None:
        line = f"src/octowright/x.py:1:1: {severity}[some-rule] something"

        assert _load()._extract_diagnostics([line]) == {line}

    def test_the_summary_line_is_not_a_diagnostic(self) -> None:
        """`Found 1 diagnostic` would otherwise be counted as a finding, and
        would never match a baseline entry — failing the gate on every run."""
        assert _load()._extract_diagnostics([_CONCISE_ERROR, _SUMMARY]) == {_CONCISE_ERROR}

    def test_the_all_clear_line_is_not_a_diagnostic(self) -> None:
        assert _load()._extract_diagnostics(["All checks passed!"]) == set()

    def test_blank_lines_are_ignored(self) -> None:
        assert _load()._extract_diagnostics(["", "   ", _CONCISE_ERROR]) == {_CONCISE_ERROR}

    def test_prose_mentioning_a_rule_is_not_collected(self) -> None:
        """Only the `path:line:col: severity[rule]` shape counts, so a message
        quoting a rule name cannot be mistaken for a diagnostic of its own."""
        assert _load()._extract_diagnostics(["note: error[call-non-callable] is suppressed here"]) == set()


class TestBaselineRatchet:
    def test_a_baselined_diagnostic_does_not_fail_the_gate(self, tmp_path: Path) -> None:
        module = _load()
        baseline = tmp_path / "b.json"
        baseline.write_text(f'{{"allow_diagnostics": ["{_CONCISE_ERROR}"]}}', encoding="utf-8")

        assert module._load_baseline(baseline) == {_CONCISE_ERROR}
        assert not (module._extract_diagnostics([_CONCISE_ERROR]) - module._load_baseline(baseline))

    def test_an_unbaselined_diagnostic_is_new(self, tmp_path: Path) -> None:
        module = _load()
        baseline = tmp_path / "b.json"
        baseline.write_text('{"allow_diagnostics": []}', encoding="utf-8")

        assert module._extract_diagnostics([_CONCISE_ERROR]) - module._load_baseline(baseline) == {_CONCISE_ERROR}
