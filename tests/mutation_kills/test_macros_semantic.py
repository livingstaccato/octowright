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
