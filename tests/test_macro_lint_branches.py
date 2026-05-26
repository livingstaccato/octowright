# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-precision tests for octowright.macros.lint.

Pins exact severity, code, message substring, and action_index for every
Issue emit point. Companion file:
``tests/test_macro_lint_helpers.py`` covers helper-function boundaries
and catalogue invariants.

Goal: kill survived mutmut mutations that exploit imprecise assertions
(severity flipped from "error" to "warning", action_index shifted by one,
code or message tweaked).
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.lint import Issue, lint_macro


def _macro(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": "x", "description": None, "parameters": [], "actions": actions}


def _only(issues: list[Issue], code: str) -> Issue:
    matches = [i for i in issues if i.code == code]
    assert len(matches) == 1, f"expected exactly one {code!r}, got {[i.code for i in issues]}"
    return matches[0]


# ---------------------------------------------------------------------------
# severity pinning — every code emits the documented severity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("macro_in", "code", "severity"),
    [
        ({"name": "x"}, "missing_actions", "error"),
        ({"actions": "nope"}, "actions_not_list", "error"),
        ({"actions": [42]}, "action_not_object", "error"),
        ({"actions": [{}]}, "missing_action_field", "error"),
        ({"actions": [{"action": "click"}]}, "missing_required_field", "error"),
        ({"actions": [{"action": "do_unknown"}]}, "unknown_action", "warning"),
        ({"actions": [{"action": "snapshot"}]}, "lifecycle_in_macro", "warning"),
        ({"actions": [{"action": "macro_call"}]}, "macro_call_invalid_name", "error"),
        (
            {"actions": [{"action": "macro_call", "name": "x", "args": []}]},
            "macro_call_invalid_args",
            "error",
        ),
        ({"actions": [{"action": "if_selector"}]}, "if_selector_missing_selector", "error"),
        (
            {"actions": [{"action": "if_selector", "selector": "#x"}]},
            "if_selector_empty_branches",
            "warning",
        ),
        ({"actions": [{"action": "try"}]}, "try_missing_actions", "error"),
        ({"actions": [{"action": "try", "actions": []}]}, "try_empty_actions", "warning"),
        ({"actions": [{"action": "try_each"}]}, "try_each_missing_branches", "error"),
        ({"actions": [{"action": "try_each", "branches": []}]}, "try_each_empty_branches", "error"),
        ({"actions": [{"action": "try_each", "branches": [[]]}]}, "try_each_branch_empty", "warning"),
        (
            {"actions": [{"action": "fill", "selector": "#x", "value": "me@octowright.test"}]},
            "looks_like_credential",
            "warning",
        ),
    ],
)
def test_each_code_has_correct_severity(macro_in: dict[str, Any], code: str, severity: str) -> None:
    """Pin severity per code so a flip mutation (error<->warning) cannot survive."""
    issues = lint_macro(macro_in)
    issue = _only(issues, code)
    assert issue.severity == severity, f"{code} should be {severity}, got {issue.severity}"


# ---------------------------------------------------------------------------
# action_index pinning — None for whole-macro; integer for per-action
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_macro", [{"name": "x"}, {"actions": "nope"}])
def test_whole_macro_issues_have_none_action_index(missing_macro: dict[str, Any]) -> None:
    """Whole-macro issues use action_index=None, not 0."""
    issues = lint_macro(missing_macro)
    assert len(issues) == 1
    assert issues[0].action_index is None


def test_first_action_index_is_zero_not_one() -> None:
    """Pin index=0 for the first action so an off-by-one mutation cannot survive."""
    issues = lint_macro(_macro([{"action": "click"}]))
    issue = _only(issues, "missing_required_field")
    assert issue.action_index == 0


def test_second_action_index_is_one() -> None:
    """Pin index=1 for the second action."""
    issues = lint_macro(
        _macro(
            [
                {"action": "navigate", "url": "https://x"},
                {"action": "click"},
            ]
        )
    )
    issue = _only(issues, "missing_required_field")
    assert issue.action_index == 1


def test_last_action_index_matches_position() -> None:
    """Pin index for the last position in a longer macro."""
    actions = [{"action": "navigate", "url": "https://x"}] * 4
    actions.append({"action": "click"})
    issues = lint_macro(_macro(actions))
    issue = _only(issues, "missing_required_field")
    assert issue.action_index == 4


def test_nested_issue_action_index_is_outer_not_inner() -> None:
    """Recursion preserves outer index — inside-the-then index 0 should NOT appear."""
    macro = _macro(
        [
            {"action": "navigate", "url": "https://x"},
            {
                "action": "if_selector",
                "selector": "#root",
                "then": [{"action": "click"}],
            },
        ]
    )
    issues = lint_macro(macro)
    assert len(issues) == 1
    assert issues[0].code == "missing_required_field"
    assert issues[0].action_index == 1


def test_nested_issue_inside_else_uses_outer_index() -> None:
    """Same recursion guarantee for the 'else' branch."""
    macro = _macro(
        [
            {
                "action": "if_selector",
                "selector": "#root",
                "then": [{"action": "click", "selector": "#a"}],
                "else": [{"action": "fill", "selector": "#b"}],
            },
        ]
    )
    issues = lint_macro(macro)
    assert len(issues) == 1
    assert issues[0].action_index == 0


def test_try_each_branch_index_in_message() -> None:
    """The 'branch [N] is empty' message must reflect the actual branch index."""
    macro = _macro(
        [
            {
                "action": "try_each",
                "branches": [
                    [{"action": "click", "selector": "#a"}],
                    [],
                    [{"action": "click", "selector": "#c"}],
                    [],
                ],
            }
        ]
    )
    issues = lint_macro(macro)
    branch_warnings = [i for i in issues if i.code == "try_each_branch_empty"]
    assert len(branch_warnings) == 2
    assert "[1]" in branch_warnings[0].message
    assert "[3]" in branch_warnings[1].message


# ---------------------------------------------------------------------------
# message substring pinning — kill mutations that retitle codes/messages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("macro_in", "code", "must_contain"),
    [
        ({"name": "x"}, "missing_actions", "no 'actions' field"),
        ({"actions": "nope"}, "actions_not_list", "not a list"),
        ({"actions": [42]}, "action_not_object", "not a JSON object"),
        ({"actions": [{}]}, "missing_action_field", "has no 'action' field"),
        ({"actions": [{"action": "navigate"}]}, "missing_required_field", "'url'"),
        ({"actions": [{"action": "do_unknown"}]}, "unknown_action", "'do_unknown'"),
        ({"actions": [{"action": "snapshot"}]}, "lifecycle_in_macro", "'snapshot'"),
        ({"actions": [{"action": "macro_call"}]}, "macro_call_invalid_name", "non-empty string"),
        (
            {"actions": [{"action": "macro_call", "name": "x", "args": "no"}]},
            "macro_call_invalid_args",
            "must be a dict",
        ),
        ({"actions": [{"action": "if_selector"}]}, "if_selector_missing_selector", "'selector'"),
        (
            {"actions": [{"action": "if_selector", "selector": "#x"}]},
            "if_selector_empty_branches",
            "'then' or 'else'",
        ),
        ({"actions": [{"action": "try"}]}, "try_missing_actions", "must be a list"),
        ({"actions": [{"action": "try", "actions": []}]}, "try_empty_actions", "empty"),
        ({"actions": [{"action": "try_each"}]}, "try_each_missing_branches", "must be a list"),
        ({"actions": [{"action": "try_each", "branches": []}]}, "try_each_empty_branches", "empty"),
    ],
)
def test_message_substrings(macro_in: dict[str, Any], code: str, must_contain: str) -> None:
    """Pin a stable substring per code so message edits in source surface as test failures."""
    issues = lint_macro(macro_in)
    issue = _only(issues, code)
    assert must_contain in issue.message, f"{code} message should contain {must_contain!r}, got {issue.message!r}"


def test_credential_warning_includes_field_and_value() -> None:
    """The credential warning quotes the literal value AND the field key."""
    issues = lint_macro(_macro([{"action": "fill", "selector": "#e", "value": "me@octowright.test"}]))
    issue = _only(issues, "looks_like_credential")
    assert "me@octowright.test" in issue.message
    assert "'value'" in issue.message
    assert "{{email}}" in issue.message


# ---------------------------------------------------------------------------
# Multi-action ordering: action_index reflects position
# ---------------------------------------------------------------------------


def test_multi_action_indices_are_distinct_and_correct() -> None:
    """Three failing actions: indices 0, 1, 2 in order."""
    macro = _macro(
        [
            {"action": "click"},
            {"action": "fill", "selector": "#x"},
            {"action": "press_key"},
        ]
    )
    issues = lint_macro(macro)
    field_errs = [i for i in issues if i.code == "missing_required_field"]
    assert [i.action_index for i in field_errs] == [0, 1, 2]


# ─── Regression: malformed actions/branches values must not crash lint ──────


class TestNonListBranchesDontCrashLint:
    """Repro for the pre-fix bug: `_lint_try`/`_lint_try_each`/`_lint_if_selector`
    fell through to `for sub in field or []:` which iterated truthy non-list values
    (TypeError on int, char-by-char on string) instead of skipping them.
    """

    def test_try_actions_as_int_does_not_raise(self) -> None:
        """try with actions=int — _check_try flags missing_actions; iteration must skip."""
        macro = _macro([{"action": "try", "actions": 1}])
        issues = lint_macro(macro)
        codes = [i.code for i in issues]
        assert "try_missing_actions" in codes  # validator caught it
        # No TypeError, no extra child-action diagnostics.

    def test_try_actions_as_string_does_not_iterate_chars(self) -> None:
        """try with actions='oops' — formerly produced 4 spurious child diagnostics."""
        macro = _macro([{"action": "try", "actions": "oops"}])
        issues = lint_macro(macro)
        codes = [i.code for i in issues]
        assert "try_missing_actions" in codes
        # No extra diagnostics from iterating the 4 characters.
        assert codes.count("missing_required_field") == 0
        assert codes.count("action_not_object") == 0

    def test_try_each_branches_as_int_does_not_raise(self) -> None:
        """try_each with branches=int — validator catches; iteration skipped."""
        macro = _macro([{"action": "try_each", "branches": 7}])
        issues = lint_macro(macro)
        assert any(i.code == "try_each_missing_branches" for i in issues)

    def test_try_each_branches_as_string_does_not_raise(self) -> None:
        """try_each with branches='oops' — same as int but for a string."""
        macro = _macro([{"action": "try_each", "branches": "oops"}])
        issues = lint_macro(macro)
        assert any(i.code == "try_each_missing_branches" for i in issues)

    def test_if_selector_then_as_int_does_not_raise(self) -> None:
        """if_selector with then=int — must not TypeError on `for sub in then`."""
        macro = _macro([{"action": "if_selector", "selector": "#x", "then": 1}])
        # Pre-fix: TypeError. Post-fix: silently skipped (no recursion into non-list).
        lint_macro(macro)

    def test_if_selector_else_as_string_no_char_recursion(self) -> None:
        """if_selector with else='oops' — must not iterate chars."""
        macro = _macro(
            [
                {
                    "action": "if_selector",
                    "selector": "#x",
                    "then": [{"action": "click", "selector": "#a"}],
                    "else": "oops",
                }
            ]
        )
        issues = lint_macro(macro)
        # The valid then-branch contributed (one click action — no missing fields).
        # No char-by-char iteration of 'oops'.
        codes = [i.code for i in issues]
        assert codes.count("action_not_object") == 0
