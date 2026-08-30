# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.macros.lint import lint_macro
from octowright.macros.lint_fields import allowed_fields_for
from octowright.macros.runtime import _ACTION_MAP


def test_action_is_dispatchable() -> None:
    assert _ACTION_MAP["a11y_dragdrop"] == "a11y_dragdrop"


def test_allowed_fields_derive_from_the_session_signature() -> None:
    """No hand-maintained field list -- the signature is the source of truth."""
    allowed = allowed_fields_for("a11y_dragdrop")
    assert {"source_selector", "nav_key", "verify_js", "max_nav_steps"} <= allowed


def _macro(action: dict) -> dict:
    return {"name": "m", "actions": [action]}


def test_lint_requires_a_source_selector() -> None:
    issues = lint_macro(_macro({"action": "a11y_dragdrop", "verify_js": "() => true"}))
    assert any("source_selector" in str(i) for i in issues)


def test_lint_rejects_zero_verify_fields() -> None:
    issues = lint_macro(_macro({"action": "a11y_dragdrop", "source_selector": "#i"}))
    assert any("exactly one verify_" in str(i) for i in issues)


def test_lint_rejects_two_verify_fields() -> None:
    issues = lint_macro(
        _macro(
            {
                "action": "a11y_dragdrop",
                "source_selector": "#i",
                "verify_js": "() => true",
                "verify_text_contains": "done",
            }
        )
    )
    assert any("exactly one verify_" in str(i) for i in issues)


def test_lint_accepts_a_well_formed_action() -> None:
    issues = lint_macro(_macro({"action": "a11y_dragdrop", "source_selector": "#i", "verify_js": "() => true"}))
    assert issues == []


def test_lint_counts_an_empty_verify_field_as_unset() -> None:
    """Arity is counted by TRUTHINESS, so this shape is legal -- which is why
    the engine's own verify dispatch has to agree. Dispatching on
    ``is not None`` there evaluated the empty ``verify_js`` and never ran the
    author's text check.
    """
    issues = lint_macro(
        _macro(
            {
                "action": "a11y_dragdrop",
                "source_selector": "#i",
                "verify_js": "",
                "verify_text_contains": "Done",
            }
        )
    )
    assert issues == []


@pytest.mark.parametrize("field", ["verify_js", "grabbed_predicate_js"])
def test_lint_scans_the_js_fields_for_literal_credentials(field: str) -> None:
    """Both fields reach ``evaluate`` under a name that is not ``expression``,
    so the code-shaped credential scan has to know them by name.
    """
    action = {
        "action": "a11y_dragdrop",
        "source_selector": "#i",
        field: "() => fetch('/x', {headers: {a: 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345'}})",  # pragma: allowlist secret
    }
    if field != "verify_js":
        action["verify_js"] = "() => true"
    issues = lint_macro(_macro(action))
    assert any(i.code == "looks_like_credential" for i in issues), issues
