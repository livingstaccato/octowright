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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _macro(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": "x", "description": None, "parameters": [], "actions": actions}


def _codes(issues: list[Issue]) -> list[str]:
    return [i.code for i in issues]


# ---------------------------------------------------------------------------
# Whole-macro structural rules
# ---------------------------------------------------------------------------


def test_clean_macro_returns_no_issues() -> None:
    macro = _macro(
        [
            {"action": "navigate", "url": "https://example.com"},
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
    action = {"action": "macro_call", "name": "other", "args": {"email": "alice"}}
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
    issues = lint_macro(_macro([{"action": "fill", "selector": "input[name=email]", "value": "me@example.com"}]))
    assert _codes(issues) == ["looks_like_credential"]
    assert issues[0].severity == "warning"
    assert "me@example.com" in issues[0].message


def test_looks_like_credential_password() -> None:
    # 12+ chars, has letters, digits, special
    issues = lint_macro(_macro([{"action": "fill", "selector": "input[name=password]", "value": "Hunter2!secret"}]))
    assert _codes(issues) == ["looks_like_credential"]


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
            {"action": "fill", "selector": "input", "value": "me@example.com"},  # credential warning
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
            {"action": "navigate", "url": "https://example.com"},
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
