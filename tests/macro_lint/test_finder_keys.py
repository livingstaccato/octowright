# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A locator needs a FINDER, and the ``*_exact`` flags are not finders.

``build_locator`` requires exactly one of role/label/text/test_id. Everything
else in ``SEMANTIC_LOCATOR_KEYS`` is a modifier: ``role_name`` narrows a role,
and the three ``*_exact`` flags narrow the match. Any code that treats "has a
semantic key" as "has a locator" is wrong, and 0.14.4 widened that tuple from 6
keys to 8, so the two places that made that assumption got worse rather than
staying still.
"""

from __future__ import annotations

import pytest

from octowright.macros.lint import lint_macro
from octowright.macros.repair import semantic_replacement
from octowright.macros.substitution import SEMANTIC_LOCATOR_KEYS


@pytest.mark.parametrize(
    ("action", "why"),
    [
        (
            {"action": "click", "selector": "#buy", "text_exact": True},
            "a bare modifier is not a locator, and the CSS selector still works",
        ),
        (
            {"action": "fill", "selector": "#pw", "value": "v", "label_exact": False},
            "the guard tested `is not None`, so even False qualified",
        ),
        (
            {"action": "click", "selector": "#buy", "role_name": "Save"},
            "role_name without role cannot resolve either",
        ),
    ],
)
def test_modifier_only_actions_get_no_repair_suggestion(action: dict[str, object], why: str) -> None:
    """Suggesting `click_by` here replaces a working selector with nothing.

    macro_repair_apply saves the suggestion in place, after which replay raises
    `ValueError: exactly one of role/label/text/test_id must be set; got: []`
    with no selector left to fall back to.
    """
    assert semantic_replacement(action, semantic_keys=SEMANTIC_LOCATOR_KEYS) is None, why


def test_a_real_finder_still_produces_a_repair_and_keeps_its_modifiers() -> None:
    replacement = semantic_replacement(
        {"action": "click", "selector": "#buy", "text": "Buy now", "text_exact": True},
        semantic_keys=SEMANTIC_LOCATOR_KEYS,
    )
    assert replacement == {"action": "click_by", "text": "Buy now", "text_exact": True}


@pytest.mark.parametrize("action", [{"action": "click_by", "role_name": "Save"}, {"action": "fill_by", "value": "v"}])
def test_lint_requires_a_real_finder_not_just_a_modifier(action: dict[str, object]) -> None:
    """`role_name` alone satisfied the old check but raises ValueError on replay."""
    codes = [i.code for i in lint_macro({"name": "t", "actions": [action]})]
    assert "missing_required_field" in codes


def test_selector_is_not_blessed_for_get_text_by() -> None:
    """`get_text_by` takes **finders, so `selector` binds at the session method
    and only explodes deeper, in `build_locator` — which has no `selector`
    parameter. The signature probe cannot see that, so it is pinned here.

    click_by/fill_by are different: `_dispatch_click_or_fill` really does fall
    back to `session.click(selector=...)`, so `selector` is legal for those.
    """
    from octowright.macros.lint_fields import allowed_fields_for

    assert "selector" not in allowed_fields_for("get_text_by")
    assert "selector" in allowed_fields_for("click_by")
    assert "selector" in allowed_fields_for("fill_by")
