# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

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
