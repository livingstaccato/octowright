# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_lint``'s issues, compared whole.

An issue's severity decides whether the dashboard will save the macro (it
refuses on any error), its code is the stable identifier tools key on, its
index says which step to fix -- the OUTER step for one found inside a
conditional -- and its message is what the author reads. So each case below
asserts the complete issue list rather than probing one attribute.

Every offending step sits at index 1 behind a clean ``navigate``, so an index
that drifted to 0 or to None is visible.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest

from octowright.macros.lint import lint_macro

CLEAN = {"action": "navigate", "url": "https://app.test/"}


def _issues(step: Any) -> list[dict[str, Any]]:
    return [asdict(issue) for issue in lint_macro({"actions": [CLEAN, step]})]


def _error(code: str, message: str, index: int | None = 1) -> dict[str, Any]:
    return {"severity": "error", "code": code, "message": message, "action_index": index}


def _warning(code: str, message: str, index: int | None = 1) -> dict[str, Any]:
    return {"severity": "warning", "code": code, "message": message, "action_index": index}


def test_a_stray_screenshot_field_is_a_warning_that_says_replay_drops_it() -> None:
    assert _issues({"action": "screenshot", "path": "a.png", "fullpage": True}) == [
        _warning(
            "unknown_field",
            "action 'screenshot' does not accept field 'fullpage' — replay ignores it, so the action will not "
            "do what the field says; check the spelling against the tool's parameters",
        )
    ]


def test_a_malformed_forward_on_redirect_is_an_error_carrying_the_refusal() -> None:
    step = {
        "action": "inject_headers",
        "pattern": "https://app.test/**",
        "headers": {"X-A": "1"},
        "forward_on_redirect": {"X-B": True},
    }
    assert _issues(step) == [
        _error(
            "bad_forward_on_redirect",
            "forward_on_redirect names 'X-B', which this step's headers do not carry",
        )
    ]


def test_forward_on_redirect_is_judged_only_where_replay_reads_it() -> None:
    """mock_route's headers are response headers: no redirect to opt into."""
    step = {
        "action": "mock_route",
        "pattern": "https://app.test/**",
        "headers": {"X-A": "1"},
        "forward_on_redirect": "nonsense",
    }
    assert [issue["code"] for issue in _issues(step)] == ["unknown_field"]


def test_an_inject_headers_without_the_opt_in_is_not_a_forward_error() -> None:
    step = {"action": "inject_headers", "pattern": "https://app.test/**", "headers": {"X-A": "1"}}
    assert _issues(step) == []


def test_a_credential_header_the_export_refuses_is_named() -> None:
    step = {
        "action": "inject_headers",
        "pattern": "https://app.test/**",
        "headers": {"Authorization": "{{token}}", "Cookie": "{{session}}", "X-Plain": "1"},
    }
    assert _issues(step) == [
        _warning(
            "export_refuses_credential_header",
            "inject_headers header(s) Authorization, Cookie hold a credential; an exported script "
            "(macro_export_cli) refuses this step, since its navigation redirects carry them, unless "
            'forward_on_redirect names them, such as {"Authorization": true}. macro_run accepts it',
        )
    ]


def test_a_malformed_allowed_origins_is_an_error_carrying_the_refusal() -> None:
    step = {"action": "fill", "selector": "#pw", "value": "{{password}}", "allowed_origins": "https://login.test"}
    assert _issues(step) == [
        _error(
            "bad_allowed_origins",
            "allowed_origins must be a list of origins such as ['https://login.example'], got str",
        )
    ]


def test_both_spellings_of_a_renamed_field_are_an_error() -> None:
    step = {"action": "mock_route", "pattern": "https://a.test/**", "url_pattern": "https://b.test/**"}
    assert _issues(step) == [
        _error(
            "ambiguous_field",
            "action 'mock_route' carries both 'pattern' and 'url_pattern', which are the same field — "
            "replay refuses the action when they differ, and a later edit to one leaves them differing; keep one",
        )
    ]


def test_two_locator_fields_are_ambiguous() -> None:
    assert _issues({"action": "click_by", "role": "button", "text": "Save"}) == [
        _error(
            "ambiguous_locator",
            "action 'click_by' sets 2 locator fields (role, text) — replay requires exactly one of "
            "role/label/text/test_id and raises ValueError otherwise; keep one",
        )
    ]


_TRIGGER = "action 'upload_files' requires exactly one trigger: selector or one of role/label/text/test_id"


@pytest.mark.parametrize(
    ("triggers", "detail"),
    [
        ({}, ""),
        ({"selector": "#f", "label": "File"}, "; got selector, label"),
        ({"role": "button", "test_id": "up"}, "; got role, test_id"),
    ],
)
def test_upload_files_trigger_arity(triggers: dict[str, Any], detail: str) -> None:
    assert _issues({"action": "upload_files", "paths": ["a.txt"], **triggers}) == [
        _error("invalid_upload_trigger", _TRIGGER + detail)
    ]


def test_upload_files_paths_and_modifiers() -> None:
    assert _issues({"action": "upload_files", "paths": [], "label": "F", "role_name": "x"}) == [
        _error("invalid_upload_paths", "action 'upload_files' field 'paths' must be a non-empty list"),
        _error("invalid_upload_trigger_modifier", "action 'upload_files' field 'role_name' requires 'role'"),
    ]


def test_a_drag_without_ends() -> None:
    assert _issues({"action": "drag"}) == [
        _error("missing_required_field", "action 'drag' is missing required field 'source' (or 'source_selector')"),
        _error("missing_required_field", "action 'drag' is missing required field 'target' (or 'target_selector')"),
    ]


def test_an_a11y_dragdrop_needs_exactly_one_verify_field() -> None:
    step = {"action": "a11y_dragdrop", "source_selector": "#a", "verify_js": "x", "verify_selector_gone": "#b"}
    assert _issues(step) == [
        _error(
            "missing_required_field",
            "action 'a11y_dragdrop' requires exactly one verify_* field (verify_js, verify_selector_appears, "
            "verify_selector_gone, verify_text_contains), got 2",
        )
    ]


def test_if_selector_issues_and_its_nested_steps_report_the_outer_index() -> None:
    assert _issues({"action": "if_selector"}) == [
        _error("if_selector_missing_selector", "if_selector is missing required field 'selector'"),
        _warning(
            "if_selector_empty_branches", "if_selector has no actions in either 'then' or 'else' (no-op condition)"
        ),
    ]
    step = {"action": "if_selector", "selector": "#a", "then": [{"action": "click"}], "else": [{"action": "launch"}]}
    assert _issues(step) == [
        _error("missing_required_field", "action 'click' is missing required field 'selector'"),
        _warning("lifecycle_in_macro", "action 'launch' will be silently skipped at runtime; consider removing"),
    ]


def test_macro_call_issues() -> None:
    assert _issues({"action": "macro_call", "name": "", "args": ["x"]}) == [
        _error("macro_call_invalid_name", "macro_call is missing required non-empty string field 'name'"),
        _error("macro_call_invalid_args", "macro_call field 'args' must be a dict when provided"),
    ]


def test_try_issues() -> None:
    assert _issues({"action": "try"}) == [
        _error("try_missing_actions", "try is missing required field 'actions' (must be a list)")
    ]
    assert _issues({"action": "try", "actions": []}) == [
        _warning("try_empty_actions", "try has an empty 'actions' list")
    ]


def test_try_each_issues() -> None:
    assert _issues({"action": "try_each"}) == [
        _error("try_each_missing_branches", "try_each is missing required field 'branches' (must be a list)")
    ]
    assert _issues({"action": "try_each", "branches": []}) == [
        _error("try_each_empty_branches", "try_each has an empty 'branches' list")
    ]
    assert _issues({"action": "try_each", "branches": [[CLEAN], [], "x"]}) == [
        _warning("try_each_branch_empty", "try_each branch [1] is empty"),
        _warning("try_each_branch_empty", "try_each branch [2] is empty"),
    ]


def test_malformed_steps_and_unknown_actions() -> None:
    assert _issues("navigate") == [_error("action_not_object", "action at index 1 is not a JSON object")]
    assert _issues({"action": ""}) == [_error("missing_action_field", "action at index 1 has no 'action' field")]
    assert _issues({"action": "teleport"}) == [
        _warning("unknown_action", "unknown action 'teleport' — could be a typo or future action type")
    ]


def test_a_literal_credential_reports_the_step() -> None:
    step = {"action": "fill", "selector": "#email", "value": "someone@example.com"}
    assert _issues(step) == [
        _warning(
            "looks_like_credential",
            "field 'value' looks like a literal credential (value redacted) — consider {{email}} parameterization",
        )
    ]


def test_whole_macro_issues() -> None:
    assert [asdict(issue) for issue in lint_macro({})] == [
        _error("missing_actions", "macro has no 'actions' field", None)
    ]
    assert [asdict(issue) for issue in lint_macro({"actions": {}})] == [
        _error("actions_not_list", "macro 'actions' field is not a list", None)
    ]
