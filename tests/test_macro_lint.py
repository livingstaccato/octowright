# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from octowright.macros.lint import Issue, lint_macro
from octowright.macros.runtime import _ACTION_MAP

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _macro(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": "x", "description": None, "parameters": [], "actions": actions}


def _codes(issues: list[Issue]) -> list[str]:
    return [i.code for i in issues]


def test_runtime_actions_are_known_to_linter() -> None:
    issues = lint_macro({"actions": [{"action": name} for name in _ACTION_MAP]})

    assert "unknown_action" not in _codes(issues)


@pytest.mark.parametrize(
    ("action_name", "required_field"),
    [
        ("hover", "selector"),
        ("select_option", "selector"),
        ("drag", "source"),
        ("drag", "target"),
        ("resize", "width"),
        ("resize", "height"),
        ("open_url", "url"),
        ("switch_page", "index"),
        ("close_page", "index"),
    ],
)
def test_runtime_actions_missing_required_fields_are_linted(action_name: str, required_field: str) -> None:
    issues = lint_macro(_macro([{"action": action_name}]))
    missing = [i for i in issues if i.code == "missing_required_field"]
    assert missing, f"expected missing_required_field for {action_name}"
    assert any(required_field in i.message for i in missing), [i.message for i in missing]


# ---------------------------------------------------------------------------
# Whole-macro structural rules
# ---------------------------------------------------------------------------


def test_clean_macro_returns_no_issues() -> None:
    macro = _macro(
        [
            {"action": "navigate", "url": "https://octowright.com"},
            {"action": "fill", "selector": "input[name=email]", "value": "{{email}}"},
            {"action": "fill", "selector": "input[name=password]", "value": "{{password}}"},
            {"action": "click", "selector": "button[type=submit]"},
            {"action": "expect_url", "pattern": r"/dashboard"},
        ]
    )
    assert lint_macro(macro) == []


def test_missing_actions_field_is_error() -> None:
    issues = lint_macro({"name": "x"})
    assert _codes(issues) == ["missing_actions"]
    assert issues[0].severity == "error"
    assert issues[0].action_index is None


def test_actions_not_list_is_error() -> None:
    issues = lint_macro({"actions": "nope"})
    assert _codes(issues) == ["actions_not_list"]


def test_action_not_object_is_error() -> None:
    issues = lint_macro(_macro(["just-a-string"]))  # type: ignore[list-item]
    assert _codes(issues) == ["action_not_object"]
    assert issues[0].action_index == 0


def test_missing_action_field_is_error() -> None:
    issues = lint_macro(_macro([{"selector": "#x"}]))
    assert _codes(issues) == ["missing_action_field"]


# ---------------------------------------------------------------------------
# missing_required_field — one test per simple action requirement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected_field"),
    [
        ({"action": "navigate"}, "url"),
        ({"action": "click"}, "selector"),
        ({"action": "click_by"}, "locator"),
        ({"action": "fill", "selector": "#x"}, "value"),
        ({"action": "fill", "value": "v"}, "selector"),
        ({"action": "fill_by", "value": "v"}, "locator"),
        ({"action": "type", "selector": "#x"}, "text"),
        ({"action": "type", "text": "hi"}, "selector"),
        ({"action": "press_key"}, "key"),
        ({"action": "expect_url"}, "pattern"),
        ({"action": "expect_text", "selector": "#x"}, "text"),
        ({"action": "expect_text", "text": "hi"}, "selector"),
        ({"action": "expect_selector"}, "selector"),
        ({"action": "expect_js"}, "expression"),
        ({"action": "mock_route"}, "pattern"),
    ],
)
def test_missing_required_field(action: dict[str, Any], expected_field: str) -> None:
    issues = lint_macro(_macro([action]))
    codes = _codes(issues)
    assert "missing_required_field" in codes, f"expected missing_required_field, got {codes}"
    msgs = [i.message for i in issues if i.code == "missing_required_field"]
    if expected_field == "locator":
        assert any("locator field" in m for m in msgs), f"expected locator requirement mention in {msgs}"
    else:
        assert any(f"'{expected_field}'" in m for m in msgs), f"expected mention of {expected_field!r} in {msgs}"


def test_click_by_rejects_missing_locator() -> None:
    issues = lint_macro(_macro([{"action": "click_by"}]))
    codes = [i.code for i in issues]
    assert "missing_required_field" in codes
    msg = next(i.message for i in issues if i.code == "missing_required_field")
    assert "locator" in msg


def test_fill_by_requires_value() -> None:
    issues = lint_macro(_macro([{"action": "fill_by", "role": "textbox"}]))
    codes = [i.code for i in issues]
    assert "missing_required_field" in codes
    msgs = [i.message for i in issues if i.code == "missing_required_field"]
    assert any("'value'" in msg for msg in msgs)


# ---------------------------------------------------------------------------
# unknown_action / lifecycle_in_macro
# ---------------------------------------------------------------------------


def test_unknown_action_is_warning() -> None:
    issues = lint_macro(_macro([{"action": "do_the_thing", "foo": "bar"}]))
    assert _codes(issues) == ["unknown_action"]
    assert issues[0].severity == "warning"


def test_macro_call_is_valid() -> None:
    action = {"action": "macro_call", "name": "other", "args": {"email": "cosmo"}}
    issues = lint_macro(_macro([action]))
    assert issues == []


def test_macro_call_requires_name() -> None:
    action = {"action": "macro_call", "args": {"x": "1"}}
    issues = lint_macro(_macro([action]))
    assert _codes(issues) == ["macro_call_invalid_name"]
    assert issues[0].severity == "error"


def test_macro_call_name_must_be_string() -> None:
    action = {"action": "macro_call", "name": 123}
    issues = lint_macro(_macro([action]))
    assert _codes(issues) == ["macro_call_invalid_name"]
    assert issues[0].severity == "error"


def test_macro_call_args_must_be_dict() -> None:
    action = {"action": "macro_call", "name": "other", "args": []}
    issues = lint_macro(_macro([action]))
    assert _codes(issues) == ["macro_call_invalid_args"]
    assert issues[0].severity == "error"


def test_lifecycle_launch_is_warning() -> None:
    issues = lint_macro(_macro([{"action": "launch", "url": "https://x"}]))
    assert _codes(issues) == ["lifecycle_in_macro"]
    assert issues[0].severity == "warning"


def test_lifecycle_close_is_warning() -> None:
    issues = lint_macro(_macro([{"action": "close"}]))
    assert _codes(issues) == ["lifecycle_in_macro"]


def test_lifecycle_snapshot_is_warning() -> None:
    issues = lint_macro(_macro([{"action": "snapshot"}]))
    assert _codes(issues) == ["lifecycle_in_macro"]


# ---------------------------------------------------------------------------
# looks_like_credential
# ---------------------------------------------------------------------------


def test_looks_like_credential_email() -> None:
    issues = lint_macro(_macro([{"action": "fill", "selector": "input[name=email]", "value": "me@octowright.test"}]))
    assert _codes(issues) == ["looks_like_credential"]
    assert issues[0].severity == "warning"
    # The literal value must NOT be echoed back into the warning — the field
    # name is fine (not sensitive), the value is what leaks a credential.
    assert "me@octowright.test" not in issues[0].message
    assert "'value'" in issues[0].message


def test_looks_like_credential_password() -> None:
    # 12+ chars, has letters, digits, special
    issues = lint_macro(_macro([{"action": "fill", "selector": "input[name=password]", "value": "Hunter2!secret"}]))
    assert _codes(issues) == ["looks_like_credential"]
    assert issues[0].severity == "warning"
    # The issue still fires (detection unchanged) but the literal password
    # itself must never be echoed back in the warning text.
    assert "Hunter2!secret" not in issues[0].message


def test_placeholder_value_not_flagged_as_credential() -> None:
    issues = lint_macro(_macro([{"action": "fill", "selector": "input[name=email]", "value": "{{email}}"}]))
    assert issues == []


def test_short_password_not_flagged() -> None:
    # Under 12 chars — should not trigger.
    issues = lint_macro(_macro([{"action": "fill", "selector": "input", "value": "Abc1!"}]))
    assert issues == []


def test_letters_only_not_flagged_as_password() -> None:
    issues = lint_macro(_macro([{"action": "fill", "selector": "input", "value": "abcdefghijklmnop"}]))
    assert issues == []


# ---------------------------------------------------------------------------
# Conditional rules
# ---------------------------------------------------------------------------


def test_if_selector_missing_selector() -> None:
    issues = lint_macro(_macro([{"action": "if_selector", "then": [{"action": "click", "selector": "#x"}]}]))
    assert "if_selector_missing_selector" in _codes(issues)
    err = next(i for i in issues if i.code == "if_selector_missing_selector")
    assert err.severity == "error"


def test_if_selector_empty_branches() -> None:
    issues = lint_macro(_macro([{"action": "if_selector", "selector": "#x"}]))
    assert _codes(issues) == ["if_selector_empty_branches"]
    assert issues[0].severity == "warning"


def test_if_selector_explicit_empty_branches() -> None:
    issues = lint_macro(_macro([{"action": "if_selector", "selector": "#x", "then": [], "else": []}]))
    assert _codes(issues) == ["if_selector_empty_branches"]


def test_try_missing_actions_is_error() -> None:
    issues = lint_macro(_macro([{"action": "try"}]))
    assert _codes(issues) == ["try_missing_actions"]
    assert issues[0].severity == "error"


def test_try_empty_actions_is_warning() -> None:
    issues = lint_macro(_macro([{"action": "try", "actions": []}]))
    assert _codes(issues) == ["try_empty_actions"]
    assert issues[0].severity == "warning"


def test_try_each_missing_branches_is_error() -> None:
    issues = lint_macro(_macro([{"action": "try_each"}]))
    assert _codes(issues) == ["try_each_missing_branches"]


def test_try_each_empty_branches_is_error() -> None:
    issues = lint_macro(_macro([{"action": "try_each", "branches": []}]))
    assert _codes(issues) == ["try_each_empty_branches"]
    assert issues[0].severity == "error"


def test_try_each_branch_empty_is_warning() -> None:
    issues = lint_macro(
        _macro(
            [
                {
                    "action": "try_each",
                    "branches": [
                        [{"action": "click", "selector": "#a"}],
                        [],
                    ],
                }
            ]
        )
    )
    assert _codes(issues) == ["try_each_branch_empty"]
    assert issues[0].severity == "warning"


# ---------------------------------------------------------------------------
# Recursion
# ---------------------------------------------------------------------------


def test_nested_issue_uses_outer_action_index() -> None:
    """A click without selector inside an if_selector.then should report under the if_selector's index."""
    macro = _macro(
        [
            {"action": "navigate", "url": "https://x"},
            {
                "action": "if_selector",
                "selector": "#thing",
                "then": [{"action": "click"}],  # missing selector
            },
        ]
    )
    issues = lint_macro(macro)
    assert _codes(issues) == ["missing_required_field"]
    assert issues[0].action_index == 1  # outer if_selector's index, not 0 inside `then`


def test_nested_issue_inside_try_uses_outer_index() -> None:
    macro = _macro(
        [
            {
                "action": "try",
                "actions": [
                    {"action": "click", "selector": "#x"},
                    {"action": "fill", "selector": "#y"},  # missing value
                ],
            }
        ]
    )
    issues = lint_macro(macro)
    assert _codes(issues) == ["missing_required_field"]
    assert issues[0].action_index == 0


def test_nested_issue_inside_try_each_uses_outer_index() -> None:
    macro = _macro(
        [
            {
                "action": "try_each",
                "branches": [
                    [{"action": "press_key"}],  # missing key
                ],
            }
        ]
    )
    issues = lint_macro(macro)
    assert _codes(issues) == ["missing_required_field"]
    assert issues[0].action_index == 0


# ---------------------------------------------------------------------------
# Multi-issue integration
# ---------------------------------------------------------------------------


def test_macro_with_multiple_issues_reports_all() -> None:
    macro = _macro(
        [
            {"action": "navigate"},  # missing url -> error
            {"action": "fill", "selector": "input", "value": "me@octowright.test"},  # credential warning
            {"action": "do_unknown"},  # unknown_action warning
            {"action": "snapshot"},  # lifecycle warning
            {"action": "if_selector", "selector": "#x"},  # empty branches warning
        ]
    )
    issues = lint_macro(macro)
    codes = _codes(issues)
    assert "missing_required_field" in codes
    assert "looks_like_credential" in codes
    assert "unknown_action" in codes
    assert "lifecycle_in_macro" in codes
    assert "if_selector_empty_branches" in codes
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    assert len(errors) == 1
    assert len(warnings) == 4


# ---------------------------------------------------------------------------
# MCP tool wrapper end-to-end
# ---------------------------------------------------------------------------


def test_macro_lint_tool_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Write a macro JSON to a tmp dir, monkeypatch MACROS_DIR, call macro_lint."""
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))

    # MACROS_DIR is owned by defaults; reload it first.
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros as macros_mod
    import octowright.macros.storage as macros_storage

    importlib.reload(macros_storage)
    importlib.reload(macros_mod)
    from octowright.server import macros as server_macros_mod

    importlib.reload(server_macros_mod)

    macro = {
        "name": "linttest",
        "description": None,
        "parameters": [],
        "actions": [
            {"action": "navigate"},  # error: missing url
            {"action": "click", "selector": "#go"},  # ok
            {"action": "snapshot"},  # warning: lifecycle
        ],
    }
    macros_storage.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    (macros_storage.MACROS_DIR / "linttest.json").write_text(json.dumps(macro), encoding="utf-8")

    result = server_macros_mod.macro_lint(name="linttest")
    assert result["macro"] == "linttest"
    assert result["ok"] is False  # because of the missing-url error
    assert "1 errors" in result["summary"]
    assert "1 warnings" in result["summary"]

    codes = [i["code"] for i in result["issues"]]
    assert "missing_required_field" in codes
    assert "lifecycle_in_macro" in codes

    # Each issue dict has the documented shape.
    for issue in result["issues"]:
        assert set(issue.keys()) == {"severity", "code", "message", "action_index"}


def test_macro_lint_tool_wrapper_clean_macro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))

    # MACROS_DIR is owned by defaults; reload it first.
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros as macros_mod
    import octowright.macros.storage as macros_storage

    importlib.reload(macros_storage)
    importlib.reload(macros_mod)
    from octowright.server import macros as server_macros_mod

    importlib.reload(server_macros_mod)

    macro = {
        "name": "clean",
        "actions": [
            {"action": "navigate", "url": "https://octowright.com"},
            {"action": "click", "selector": "#go"},
        ],
    }
    macros_storage.MACROS_DIR.mkdir(parents=True, exist_ok=True)
    (macros_storage.MACROS_DIR / "clean.json").write_text(json.dumps(macro), encoding="utf-8")

    result = server_macros_mod.macro_lint(name="clean")
    assert result["ok"] is True
    assert result["issues"] == []
    assert result["summary"] == "0 issues: 0 errors, 0 warnings"


def test_macro_lint_tool_wrapper_missing_macro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))

    import octowright.macros as macros_mod
    import octowright.macros.storage as macros_storage

    importlib.reload(macros_mod)
    importlib.reload(macros_storage)
    importlib.reload(macros_mod)
    from octowright.server import macros as server_macros_mod

    importlib.reload(server_macros_mod)

    with pytest.raises(FileNotFoundError):
        server_macros_mod.macro_lint(name="does-not-exist")


# ---------------------------------------------------------------------------
# Accept side: the alternatives an "or" offers, and the emptiness an "or []" hides
#
# Every check above asserts that a BAD macro is reported. None asserted that a
# good one is not, and a rejection test cannot tell "this alternative is
# accepted" from "this alternative was never considered" -- so mutation testing
# left both `or`s in the drag check and both in `if_selector` alive. A linter
# that rejects valid input is worse than one that misses invalid input: it
# blocks the dashboard save outright.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_key", "target_key"),
    [
        ("source", "target"),
        ("source_selector", "target_selector"),
        ("source", "target_selector"),
        ("source_selector", "target"),
    ],
)
def test_drag_accepts_either_spelling_of_each_endpoint(source_key: str, target_key: str) -> None:
    """``source`` OR ``source_selector`` -- either alone is a complete endpoint.

    Both spellings exist because ``browser_drag`` accepts both, and the check
    is an ``or`` precisely so a macro need not carry the pair. Tightening it to
    an ``and`` demands both and reports every valid drag as missing a field.
    All four combinations are covered because the two endpoints are separate
    checks and a single combination leaves one of them unproven.
    """
    issues = lint_macro(_macro([{"action": "drag", source_key: "#a", target_key: "#b"}]))

    assert _codes(issues) == []


def test_if_selector_with_an_empty_selector_is_still_reported() -> None:
    """``"selector" not in action or not action.get("selector")`` -- the emptiness half.

    A present-but-empty ``selector`` is what a dashboard editor produces when
    the field is cleared, and it is exactly as broken as an absent one: the
    branch condition can never be evaluated. Only the second half of the ``or``
    catches it, so joining the two with ``and`` lets it through to replay.
    """
    issues = lint_macro(_macro([{"action": "if_selector", "selector": "", "then": [{"action": "click"}]}]))

    assert "if_selector_missing_selector" in _codes(issues)


def test_if_selector_with_only_an_else_branch_is_not_reported_as_empty() -> None:
    """``action.get("else") or []`` supplies a default, it does not discard the value.

    An ``if_selector`` that acts only when the selector is ABSENT is a real
    macro shape -- dismiss-the-banner-if-present inverted. Turning that ``or``
    into an ``and`` yields ``[]`` for a populated branch, so the emptiness
    check sees two empty branches and warns about a macro that has one.
    """
    issues = lint_macro(_macro([{"action": "if_selector", "selector": "#x", "else": [{"action": "click"}]}]))

    assert "if_selector_empty_branches" not in _codes(issues)


def test_if_selector_with_only_a_then_branch_is_not_reported_as_empty() -> None:
    """The mirror case, so neither branch's default can be broken alone."""
    issues = lint_macro(_macro([{"action": "if_selector", "selector": "#x", "then": [{"action": "click"}]}]))

    assert "if_selector_empty_branches" not in _codes(issues)
