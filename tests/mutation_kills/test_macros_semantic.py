# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exact text of the human-readable macro summaries and search intents."""

from __future__ import annotations

from octowright.macros.semantic import get_semantic_intent, summarize_action


def test_wait_for_without_a_selector_names_an_empty_one() -> None:
    assert summarize_action({"action": "wait_for"}) == "Wait for '' to appear"


def test_if_selector_without_a_then_branch_has_only_its_header() -> None:
    assert summarize_action({"action": "if_selector", "selector": "#x"}) == "If '#x' is present:"


def test_if_selector_with_both_branches_is_exact() -> None:
    action = {
        "action": "if_selector",
        "selector": "#banner",
        "present": False,
        "then": [{"action": "click", "selector": "#a"}],
        "else": [{"action": "click", "selector": "#b"}],
    }

    assert summarize_action(action, 1) == "  If '#banner' is absent:\n    - Click '#a'\n  Else:\n    - Click '#b'"


def test_try_is_exact() -> None:
    action = {
        "action": "try",
        "actions": [{"action": "click", "selector": "#a"}, {"action": "press_key", "key": "Enter"}],
    }

    assert summarize_action(action) == "Try (ignore errors):\n  - Click '#a'\n  - Press key 'Enter'"


def test_try_each_is_exact() -> None:
    action = {
        "action": "try_each",
        "branches": [[{"action": "click", "selector": "#a"}], [{"action": "click", "selector": "#b"}]],
    }

    assert summarize_action(action) == (
        "Try each branch until success:\n  Branch 1:\n    - Click '#a'\n  Branch 2:\n    - Click '#b'"
    )


def test_search_intent_without_a_query_field() -> None:
    assert get_semantic_intent([{"action": "navigate", "url": "https://ex.test/search"}]) == (
        "Search on https://ex.test/search"
    )


def test_search_intent_with_a_query() -> None:
    actions = [
        {"action": "navigate", "url": "https://ex.test/search"},
        {"action": "fill", "selector": "#q", "value": "otters"},
    ]

    assert get_semantic_intent(actions) == "Search for 'otters' on https://ex.test/search"


def test_search_intent_with_an_empty_query_names_no_term() -> None:
    actions = [
        {"action": "navigate", "url": "https://ex.test/search"},
        {"action": "fill", "selector": "#q", "value": ""},
    ]

    assert get_semantic_intent(actions) == "Search on https://ex.test/search"


def test_search_intent_with_a_valueless_type_names_no_term() -> None:
    actions = [
        {"action": "navigate", "url": "https://ex.test/search"},
        {"action": "type", "selector": "#q"},
    ]

    assert get_semantic_intent(actions) == "Search on https://ex.test/search"


def test_login_intent_lists_an_empty_fill_without_none() -> None:
    actions = [
        {"action": "navigate", "url": "https://ex.test/login"},
        {"action": "fill", "selector": "#user", "value": ""},
    ]

    assert get_semantic_intent(actions) == "Login to https://ex.test/login with #user="


SEARCH = "https://ex.test/search"


def _search(selector: str, value: str) -> str:
    return get_semantic_intent(
        [{"action": "navigate", "url": SEARCH}, {"action": "fill", "selector": selector, "value": value}]
    )


def test_a_field_with_q_inside_its_name_is_not_the_search_box() -> None:
    assert _search("#quantity", "3") == f"Search on {SEARCH}"


def test_a_value_with_q_in_it_does_not_make_its_field_the_search_box() -> None:
    assert _search("#name", "quokka") == f"Search on {SEARCH}"


def test_a_q_field_is_the_search_box_in_any_selector_spelling() -> None:
    for selector in ("#q", "q", 'input[name="q"]', "[name=q]", "#query", ".search-input"):
        assert _search(selector, "otters") == f"Search for 'otters' on {SEARCH}", selector


def test_a_search_term_containing_equals_is_kept_whole() -> None:
    assert _search("#q", "a=b") == f"Search for 'a=b' on {SEARCH}"
