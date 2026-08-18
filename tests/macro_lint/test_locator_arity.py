# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_lint`` and ``build_locator`` must agree on how many finders is legal.

``build_locator`` requires **exactly one** of role/label/text/test_id; the lint
check only required **at least one**. So a ``click_by`` carrying two finders
linted clean, was saved, and then raised ``ValueError: exactly one of
role/label/text/test_id must be set`` on replay — with no ``selector`` present,
``_dispatch_click_or_fill`` has no CSS fallback and re-raises.

This is the same lint↔replay parity defect that was already fixed in the other
direction (``role_name`` alone used to pass lint and fail replay), so the check
is derived from the same rule the runtime enforces rather than restated:
membership is ``is not None``, exactly as ``build_locator`` computes it, which
also settles ``text=""`` — a value the runtime counts as provided.
"""

from __future__ import annotations

import pytest

from octowright.macros.lint import lint_macro
from octowright.session.locators import build_locator


def _codes(actions: list[dict[str, object]]) -> list[str]:
    return [i.code for i in lint_macro({"actions": actions})]


def _action(kind: str, **fields: object) -> dict[str, object]:
    """`fill_by` also requires `value`; supply it so only locator arity is under test."""
    base: dict[str, object] = {"action": kind}
    if kind == "fill_by":
        base["value"] = "x"
    return {**base, **fields}


@pytest.mark.parametrize("kind", ["click_by", "fill_by"])
def test_two_finders_is_an_error(kind: str) -> None:
    issues = lint_macro({"actions": [_action(kind, role="button", text="Save")]})
    assert [i.code for i in issues] == ["ambiguous_locator"]
    assert issues[0].severity == "error"
    assert "exactly one" in issues[0].message


@pytest.mark.parametrize("kind", ["click_by", "fill_by"])
def test_one_finder_still_passes(kind: str) -> None:
    assert _codes([_action(kind, role="button", role_name="Save")]) == []


@pytest.mark.parametrize("kind", ["click_by", "fill_by"])
def test_no_finder_is_still_a_missing_field(kind: str) -> None:
    assert _codes([_action(kind, role_name="Save")]) == ["missing_required_field"]


def test_modifiers_do_not_count_as_finders() -> None:
    """`role_exact`/`text_exact` narrow a finder; they never are one."""
    assert _codes([{"action": "click_by", "role": "button", "role_exact": True, "text_exact": True}]) == []


@pytest.mark.asyncio
async def test_runtime_rejects_what_lint_now_rejects() -> None:
    """Pin the rule to its source rather than to a copy of it."""
    with pytest.raises(ValueError, match="exactly one"):
        await build_locator(object(), role="button", text="Save")  # type: ignore[arg-type]


def test_empty_string_finder_counts_as_provided() -> None:
    """`build_locator` filters on `is not None`, so `text=""` IS a finder.

    Linting it as *missing* made the macro unsavable through the dashboard
    (``PUT /api/macros/{name}`` 400s on any error-severity issue) for an action
    replay accepts.
    """
    assert _codes([{"action": "click_by", "text": ""}]) == []
    assert _codes([{"action": "click_by", "text": "", "role": "button"}]) == ["ambiguous_locator"]
