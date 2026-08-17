# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Helper-function and catalogue-invariant tests for octowright.macros.lint.

Companion to test_macro_lint_branches.py. Covers:

- ``_looks_like_password`` length + character-class boundaries.
- ``_looks_like_email`` accept/reject parametric set.
- ``_is_placeholder`` brace recognition.
- ``_CREDENTIAL_CANDIDATE_KEYS`` membership — every candidate key is
  inspected, non-candidate keys are not.
- ``_ARIA_LOCATOR_KEYS`` membership — each one satisfies click_by /
  fill_by's locator requirement.
- ``_REPLAY_SKIP`` members and their early-return behaviour.
- ``_check_simple`` empty-vs-None vs non-empty-falsy distinction.
- ``_check_macro_call`` name and args type validation.
- ``_check_try`` / ``_check_try_each`` non-list field rejection (constrained
  to cases that don't trigger the recursive iteration in _lint_try*).
- Action-field type validation in ``_lint_action``.
- Catalogue invariants: every ``_KNOWN_ACTIONS`` member is registered such
  that valid usage doesn't fire ``unknown_action``.

Goal mirrors _branches: pin the boundaries so mutmut mutations of constants
or of comparison/membership operators can't survive.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.lint import (
    _ARIA_LOCATOR_KEYS,
    _CREDENTIAL_CANDIDATE_KEYS,
    _KNOWN_ACTIONS,
    _REPLAY_SKIP,
    _SIMPLE_REQUIRED,
    Issue,
    _is_placeholder,
    _looks_like_email,
    _looks_like_password,
    lint_macro,
)


def _macro(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": "x", "description": None, "parameters": [], "actions": actions}


def _only(issues: list[Issue], code: str) -> Issue:
    matches = [i for i in issues if i.code == code]
    assert len(matches) == 1, f"expected exactly one {code!r}, got {[i.code for i in issues]}"
    return matches[0]


# ---------------------------------------------------------------------------
# _looks_like_password boundary
# ---------------------------------------------------------------------------


def test_password_exactly_11_chars_not_flagged() -> None:
    """Length boundary: 11 chars is below the >=12 threshold."""
    sample = "A" + "b" + "1" + "!" + ("x" * 7)  # 11-char fake fixture
    assert _looks_like_password(sample) is False


def test_password_exactly_12_chars_with_all_classes_is_flagged() -> None:
    """Length boundary: 12 chars with letters+digits+special trips."""
    sample = "A" + "bcdefg" + "1" + "!" + "xyz"  # 12-char fake fixture
    assert _looks_like_password(sample) is True


def test_password_long_but_no_digits_not_flagged() -> None:
    """Character-class boundary: digits required."""
    sample = "AbcDefGhij" + "!@#$"  # fake fixture: letters+specials, no digits
    assert _looks_like_password(sample) is False


def test_password_long_but_no_letters_not_flagged() -> None:
    """Character-class boundary: letters required."""
    assert _looks_like_password("1234567890!@#$") is False


def test_password_long_but_no_specials_not_flagged() -> None:
    """Character-class boundary: special chars required."""
    assert _looks_like_password("Abcdefghij1234") is False


def test_password_only_digits_long_not_flagged() -> None:
    """Long digit-only string still must have letters AND specials."""
    assert _looks_like_password("123456789012345") is False


# ---------------------------------------------------------------------------
# _looks_like_email boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    ["a@b.cd", "user.name+tag@example.co", "u_n-d@host-name.io", "x.y@z.q"],
)
def test_looks_like_email_accepts(candidate: str) -> None:
    """Accept set — kill regex pattern mutations that narrow the match."""
    assert _looks_like_email(candidate) is True


@pytest.mark.parametrize(
    "candidate",
    ["plain", "no-at-symbol.com", "missing.domain@", "@no-local.com", "no.dot@local", ""],
)
def test_looks_like_email_rejects(candidate: str) -> None:
    """Reject set — kill regex pattern mutations that broaden the match."""
    assert _looks_like_email(candidate) is False


# ---------------------------------------------------------------------------
# _is_placeholder
# ---------------------------------------------------------------------------


def test_is_placeholder_recognizes_double_braces() -> None:
    """{{name}} is the parameter form."""
    assert _is_placeholder("{{email}}") is True
    assert _is_placeholder("hello {{name}} world") is True


def test_is_placeholder_rejects_single_braces() -> None:
    """Single braces, raw text, and empty string all return False."""
    assert _is_placeholder("{email}") is False
    assert _is_placeholder("plain") is False
    assert _is_placeholder("") is False


# ---------------------------------------------------------------------------
# _CREDENTIAL_CANDIDATE_KEYS — every candidate key flags, others don't
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("candidate_key", sorted(_CREDENTIAL_CANDIDATE_KEYS))
def test_each_candidate_key_can_trigger_credential_warning(candidate_key: str) -> None:
    """Mutating the candidate set (removing a key) should be caught here."""
    action = {"action": "fill", "selector": "#x", "value": "ok", candidate_key: "you@somewhere.com"}
    if candidate_key == "value":
        action = {"action": "fill", "selector": "#x", "value": "you@somewhere.com"}
    if candidate_key == "url":
        # `url` is inspected by part rather than as a blob, so the credential
        # has to sit where a URL can actually carry one (see lint_urls).
        action = {"action": "fill", "selector": "#x", "value": "ok", "url": "https://u:pw@somewhere.com/x"}
    issues = lint_macro(_macro([action]))
    creds = [i for i in issues if i.code == "looks_like_credential"]
    assert len(creds) >= 1, f"key {candidate_key!r} should be inspected for credentials"


def test_selector_field_not_inspected_for_credentials() -> None:
    """Non-candidate keys like 'selector' are skipped even if they contain emails."""
    issues = lint_macro(_macro([{"action": "click", "selector": '[data-email="me@octowright.test"]'}]))
    assert all(i.code != "looks_like_credential" for i in issues)


def test_non_string_field_not_inspected() -> None:
    """isinstance(val, str) guard short-circuits."""
    issues = lint_macro(_macro([{"action": "expect_js", "expression": 12345}]))
    assert all(i.code != "looks_like_credential" for i in issues)


def test_placeholder_value_is_not_flagged_as_credential() -> None:
    """A {{name}} value is the parameterised case the rule encourages."""
    issues = lint_macro(_macro([{"action": "fill", "selector": "#x", "value": "{{password_value_here}}"}]))
    assert all(i.code != "looks_like_credential" for i in issues)


# ---------------------------------------------------------------------------
# _ARIA_LOCATOR_KEYS — each key satisfies click_by / fill_by
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("locator_key", sorted(_ARIA_LOCATOR_KEYS))
def test_click_by_accepts_each_locator_key(locator_key: str) -> None:
    """A click_by with any one ARIA locator must NOT raise missing_required_field."""
    issues = lint_macro(_macro([{"action": "click_by", locator_key: "Submit"}]))
    field_errs = [i for i in issues if i.code == "missing_required_field"]
    assert field_errs == []


@pytest.mark.parametrize("locator_key", sorted(_ARIA_LOCATOR_KEYS))
def test_fill_by_accepts_each_locator_key_when_value_present(locator_key: str) -> None:
    """fill_by needs value AND a locator — every locator key must satisfy."""
    issues = lint_macro(_macro([{"action": "fill_by", "value": "v", locator_key: "Email"}]))
    field_errs = [i for i in issues if i.code == "missing_required_field"]
    assert field_errs == []


def test_click_by_with_no_locator_field_fails() -> None:
    """No locator at all → missing_required_field with locator-specific message."""
    issues = lint_macro(_macro([{"action": "click_by"}]))
    issue = _only(issues, "missing_required_field")
    assert "click_by" in issue.message
    assert "locator" in issue.message


# ---------------------------------------------------------------------------
# _check_simple — required field None or empty string both trip the rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [None, ""])
def test_required_field_none_or_empty_string_flagged(bad_value: Any) -> None:
    """The rule blocks both None and "" but not other falsy values."""
    issues = lint_macro(_macro([{"action": "navigate", "url": bad_value}]))
    _only(issues, "missing_required_field")


def test_required_field_zero_int_is_NOT_flagged() -> None:
    """Pin: lint blocks None or "" specifically — non-empty values pass."""
    issues = lint_macro(_macro([{"action": "press_key", "key": "0"}]))
    assert all(i.code != "missing_required_field" for i in issues)


# ---------------------------------------------------------------------------
# _check_macro_call — name validation cases
# ---------------------------------------------------------------------------


def test_macro_call_with_empty_string_name_fails() -> None:
    """Pin: empty string name."""
    issues = lint_macro(_macro([{"action": "macro_call", "name": ""}]))
    _only(issues, "macro_call_invalid_name")


def test_macro_call_with_non_string_name_fails() -> None:
    """Pin: int / list / dict / None all fail the str check."""
    for bad in (None, 1, [], {}, 0.5):
        issues = lint_macro(_macro([{"action": "macro_call", "name": bad}]))
        _only(issues, "macro_call_invalid_name")


def test_macro_call_without_args_passes() -> None:
    """The args check only runs when 'args' key is present."""
    issues = lint_macro(_macro([{"action": "macro_call", "name": "other"}]))
    assert issues == []


def test_macro_call_with_dict_args_passes() -> None:
    """Pin: dict is the accepted args shape."""
    issues = lint_macro(_macro([{"action": "macro_call", "name": "x", "args": {"a": "b"}}]))
    assert issues == []


@pytest.mark.parametrize("bad_args", [[], "string", 1, None])
def test_macro_call_with_non_dict_args_fails(bad_args: Any) -> None:
    """Pin: anything that isn't a dict (even None or empty list) fails."""
    issues = lint_macro(_macro([{"action": "macro_call", "name": "x", "args": bad_args}]))
    _only(issues, "macro_call_invalid_args")


# ---------------------------------------------------------------------------
# _check_try / _check_try_each — non-list field rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_actions", [None, {}])
def test_try_with_non_list_actions_fails(bad_actions: Any) -> None:
    """Flag non-list 'actions' field. Restricted to cases where _lint_try's
    follow-up iteration over ``action.get('actions') or []`` short-circuits
    safely (None and empty dict both treat as empty)."""
    issues = lint_macro(_macro([{"action": "try", "actions": bad_actions}]))
    _only(issues, "try_missing_actions")


@pytest.mark.parametrize("bad_branches", [None, {}])
def test_try_each_with_non_list_branches_fails(bad_branches: Any) -> None:
    """Same constraint as test_try_with_non_list_actions_fails."""
    issues = lint_macro(_macro([{"action": "try_each", "branches": bad_branches}]))
    _only(issues, "try_each_missing_branches")


def test_try_omitted_actions_field_fails() -> None:
    """When 'actions' key is missing entirely, _check_try emits the error."""
    issues = lint_macro(_macro([{"action": "try"}]))
    _only(issues, "try_missing_actions")


def test_try_each_omitted_branches_field_fails() -> None:
    """When 'branches' key is missing entirely."""
    issues = lint_macro(_macro([{"action": "try_each"}]))
    _only(issues, "try_each_missing_branches")


def test_try_each_branch_that_is_not_a_list_is_warning() -> None:
    """Branches must be lists; a non-list branch is treated as empty."""
    issues = lint_macro(_macro([{"action": "try_each", "branches": ["not-a-list"]}]))
    _only(issues, "try_each_branch_empty")


# ---------------------------------------------------------------------------
# _REPLAY_SKIP — every member emits lifecycle_in_macro and skips field checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(_REPLAY_SKIP))
def test_each_replay_skip_kind_is_lifecycle_warning(kind: str) -> None:
    """Mutmut would flip _REPLAY_SKIP membership — this catches each member."""
    issues = lint_macro(_macro([{"action": kind}]))
    assert len(issues) == 1
    assert issues[0].code == "lifecycle_in_macro"
    assert kind in issues[0].message


def test_lifecycle_skips_required_field_check() -> None:
    """A 'launch' without url should NOT emit missing_required_field — lifecycle returns early."""
    issues = lint_macro(_macro([{"action": "launch"}]))
    assert all(i.code != "missing_required_field" for i in issues)


# ---------------------------------------------------------------------------
# Action field type validation — _lint_action's early returns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("non_string_action", [None, 1, [], {}, True])
def test_action_field_not_string_fails(non_string_action: Any) -> None:
    """The action field must be a non-empty string."""
    issues = lint_macro(_macro([{"action": non_string_action}]))
    _only(issues, "missing_action_field")


def test_action_field_empty_string_fails() -> None:
    """Empty string also fails the str check."""
    issues = lint_macro(_macro([{"action": ""}]))
    _only(issues, "missing_action_field")


# ---------------------------------------------------------------------------
# Action catalogue invariants — kill mutations of constant sets
# ---------------------------------------------------------------------------


def test_macro_call_is_in_known_actions() -> None:
    """Removing macro_call from _KNOWN_ACTIONS would surface as 'unknown_action'."""
    issues = lint_macro(_macro([{"action": "macro_call", "name": "n"}]))
    assert all(i.code != "unknown_action" for i in issues)


def test_replay_skip_members_are_in_known_actions() -> None:
    """Each lifecycle action must be in _KNOWN_ACTIONS so it never warns 'unknown'."""
    for kind in _REPLAY_SKIP:
        issues = lint_macro(_macro([{"action": kind}]))
        assert all(i.code != "unknown_action" for i in issues), f"{kind} should not be 'unknown'"


def test_simple_required_members_are_in_known_actions() -> None:
    """Each simple action must round-trip through the known set."""
    for kind, fields in _SIMPLE_REQUIRED.items():
        action: dict[str, Any] = {"action": kind, **{f: "value" for f in fields}}
        if kind in {"click_by", "fill_by"}:
            action["role"] = "button"
        issues = lint_macro(_macro([action]))
        assert all(i.code != "unknown_action" for i in issues), f"{kind} should not be 'unknown'"


def _build_valid_action(kind: str) -> dict[str, Any]:
    """Construct a minimally-valid action of the given kind."""
    a: dict[str, Any] = {"action": kind}
    setup = {
        "navigate": {"url": "https://x"},
        "click": {"selector": "#x"},
        "expect_selector": {"selector": "#x"},
        "fill": {"selector": "#x", "value": "v"},
        "type": {"selector": "#x", "text": "t"},
        "press_key": {"key": "Enter"},
        "evaluate": {"expression": "1"},
        "expect_url": {"pattern": "x"},
        "expect_text": {"selector": "#x", "text": "t"},
        "expect_js": {"expression": "1"},
        "mock_route": {"pattern": "*"},
        "unmock_route": {"pattern": "*"},
        "set_dialog_policy": {"policy": "dismiss"},
        "set_input_files": {"selector": "#x"},
        "click_by": {"role": "button"},
        "fill_by": {"role": "button", "value": "v"},
        "if_selector": {"selector": "#x", "then": [{"action": "click", "selector": "#a"}]},
        "try": {"actions": [{"action": "click", "selector": "#a"}]},
        "try_each": {"branches": [[{"action": "click", "selector": "#a"}]]},
        "macro_call": {"name": "n"},
    }
    a.update(setup.get(kind, {}))
    return a


def test_unknown_action_only_fires_for_truly_unknown() -> None:
    """Every action in _KNOWN_ACTIONS, given valid fields, does NOT fire unknown_action."""
    for known in _KNOWN_ACTIONS:
        action = _build_valid_action(known)
        issues = lint_macro(_macro([action]))
        assert all(i.code != "unknown_action" for i in issues), f"{known} flagged unknown: {issues}"
