# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_repair_apply``'s refusals, word for word, and its preview fallbacks."""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.repair import repair_apply, replacement_preview

SEMANTIC_KEYS = ("role", "role_name", "role_exact", "label", "text", "test_id")


def _apply(macro: dict[str, Any], index: int, *, name: str = "stored") -> tuple[dict[str, Any], list[Any]]:
    writes: list[Any] = []

    def load(requested: str) -> dict[str, Any]:
        assert requested == name
        return macro

    def write(**kwargs: Any) -> str:
        writes.append(kwargs)
        return "/macros/stored.json"

    result = repair_apply(name, index, load_macro=load, write_macro=write, semantic_keys=SEMANTIC_KEYS)
    return dict(result), writes


def _refusal(macro: dict[str, Any], index: int) -> str:
    with pytest.raises(ValueError) as excinfo:
        _apply(macro, index)
    return str(excinfo.value)


@pytest.mark.parametrize(
    ("target", "preview"),
    [
        ({"text": "Save"}, "Click by 'Save'"),
        ({"test_id": "save-btn"}, "Click by 'save-btn'"),
        ({}, "Click by semantic locator"),
    ],
)
def test_click_by_preview_falls_back_through_text_and_test_id(target: dict[str, Any], preview: str) -> None:
    assert replacement_preview({"action": "click_by", **target}) == preview


@pytest.mark.parametrize(
    ("target", "preview"),
    [
        ({"text": "Name"}, "Fill by 'Name' with 'v'"),
        ({"test_id": "name-in"}, "Fill by 'name-in' with 'v'"),
    ],
)
def test_fill_by_preview_falls_back_through_text_and_test_id(target: dict[str, Any], preview: str) -> None:
    assert replacement_preview({"action": "fill_by", "value": "v", **target}) == preview


def test_out_of_range_names_the_macro_by_its_stored_name_and_counts_its_actions() -> None:
    macro = {"name": "Checkout flow", "actions": [{"action": "click"}, {"action": "click"}]}

    assert _refusal(macro, 5) == (
        "action_index 5 is out of range for macro 'Checkout flow' (2 actions); "
        "run macro_repair_preview to see repairable action indices"
    )


def test_out_of_range_falls_back_to_the_requested_name_without_a_stored_one() -> None:
    assert _refusal({"actions": []}, 0) == (
        "action_index 0 is out of range for macro 'stored' (0 actions); "
        "run macro_repair_preview to see repairable action indices"
    )


def test_non_list_actions_count_as_zero() -> None:
    macro = {"name": "m", "actions": {"a": 1, "b": 2}}

    assert _refusal(macro, 0) == (
        "action_index 0 is out of range for macro 'm' (0 actions); "
        "run macro_repair_preview to see repairable action indices"
    )


def test_a_non_object_action_is_refused() -> None:
    assert _refusal({"name": "m", "actions": ["click"]}, 0) == "action 0 in macro 'm' is not an object; cannot repair"


def test_an_action_without_a_semantic_locator_is_refused() -> None:
    macro = {"name": "m", "actions": [{"action": "click", "selector": "#go"}]}

    assert _refusal(macro, 0) == (
        "action 0 in macro 'm' has no stored semantic locator to repair with "
        "(needs a click/fill with a selector plus role/label/text/test_id); "
        "run macro_repair_preview for a manual review prompt, or re-record the macro"
    )


def test_apply_persists_the_replacement_and_reports_the_original() -> None:
    macro = {"name": "Internal", "actions": [{"action": "click", "selector": "#go", "label": "Go"}]}

    result, writes = _apply(macro, 0)

    assert result == {
        "macro": "stored",
        "action_index": 0,
        "applied": True,
        "original_action": {"action": "click", "selector": "#go", "label": "Go"},
        "replacement_action": {"action": "click_by", "label": "Go"},
        "path": "/macros/stored.json",
    }
    assert writes == [
        {"name": "stored", "macro": {"name": "Internal", "actions": [{"action": "click_by", "label": "Go"}]}}
    ]


def test_the_reported_original_does_not_alias_the_persisted_replacement() -> None:
    label = ["Go"]
    macro = {"name": "m", "actions": [{"action": "click", "selector": "#go", "label": label}]}

    result, writes = _apply(macro, 0)
    writes[0]["macro"]["actions"][0]["label"].append("mutated later")

    assert result["original_action"]["label"] == ["Go"]
