# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exact shapes of ``macro_list``'s summary rows, family roll-up and pages."""

from __future__ import annotations

import pytest

from octowright.macros.listing import (
    MACRO_LIST_SUMMARY_DESCRIPTION_CHARS,
    select_macros,
)


def test_summary_row_of_a_sparse_entry_is_exact() -> None:
    result = select_macros([{"name": "solo"}])

    assert result == {
        "macros": [
            {
                "name": "solo",
                "description": "",
                "description_truncated": False,
                "parameters": [],
                "updated_at": None,
                "action_count": None,
            }
        ],
        "total": 1,
        "returned": 1,
        "truncated": False,
        "next_cursor": None,
        "response_mode": "summary",
        "root": None,
    }


def test_summary_row_carries_every_field_through() -> None:
    entry = {
        "name": "full",
        "description": "does a thing",
        "parameters": ["who"],
        "updated_at": "2026-01-02T00:00:00",
        "action_count": 7,
        "path": "/x/full.json",
    }

    assert select_macros([entry], root="/x")["macros"] == [
        {
            "name": "full",
            "description": "does a thing",
            "description_truncated": False,
            "parameters": ["who"],
            "updated_at": "2026-01-02T00:00:00",
            "action_count": 7,
        }
    ]


def test_description_at_exactly_the_cap_is_not_truncated() -> None:
    text = "a" * MACRO_LIST_SUMMARY_DESCRIPTION_CHARS
    row = select_macros([{"name": "m", "description": text}])["macros"][0]

    assert (row["description"], row["description_truncated"]) == (text, False)


def test_description_over_the_cap_is_cut_and_marked() -> None:
    text = "b" * MACRO_LIST_SUMMARY_DESCRIPTION_CHARS + "tail"
    row = select_macros([{"name": "m", "description": text}])["macros"][0]

    assert row["description"] == "b" * MACRO_LIST_SUMMARY_DESCRIPTION_CHARS + "…"
    assert row["description_truncated"] is True


def test_full_mode_page_is_exact() -> None:
    entry = {"name": "m", "description": "d", "path": "/r/m.json"}

    assert select_macros([entry], response_mode="full", root="/r") == {
        "macros": [entry],
        "total": 1,
        "returned": 1,
        "truncated": False,
        "next_cursor": None,
        "response_mode": "full",
        "root": "/r",
    }


def test_families_roll_up_is_exact() -> None:
    entries = [
        {"name": "map-a", "action_count": 1, "updated_at": "2026-01-01"},
        {"name": "map-b", "action_count": 2, "updated_at": "2026-01-03"},
        {"name": "grant-a", "action_count": 9, "updated_at": "2026-01-02"},
        {"name": "admin-a"},
        {},
    ]

    assert select_macros(entries, response_mode="families", root="/r") == {
        "families": [
            {"family": "map", "macros": 2, "actions": 3, "latest": "2026-01-03"},
            {"family": "grant", "macros": 1, "actions": 9, "latest": "2026-01-02"},
            {"family": "", "macros": 1, "actions": 0, "latest": ""},
            {"family": "admin", "macros": 1, "actions": 0, "latest": ""},
        ],
        "total_macros": 5,
        "total_families": 4,
        "response_mode": "families",
        "root": "/r",
    }


def test_families_with_equal_counts_rank_by_action_weight_descending() -> None:
    entries = [
        {"name": "light-a", "action_count": 1},
        {"name": "heavy-a", "action_count": 50},
    ]

    families = select_macros(entries, response_mode="families")["families"]

    assert [row["family"] for row in families] == ["heavy", "light"]


def test_families_respect_the_limit() -> None:
    entries = [{"name": f"f{i}-x"} for i in range(5)]

    result = select_macros(entries, response_mode="families", limit=2)

    assert len(result["families"]) == 2
    assert result["total_families"] == 5


def test_unknown_response_mode_is_refused_with_the_valid_modes() -> None:
    with pytest.raises(ValueError) as excinfo:
        select_macros([], response_mode="everything")

    assert str(excinfo.value) == "response_mode must be one of summary, full, families; got 'everything'"
