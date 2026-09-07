# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Bounding what ``macro_list`` puts on the MCP transport.

The tool returned every saved macro with every description, unbounded. On a
real machine that is **402,942 characters -- roughly 100k tokens -- in a single
call**, from 337 macros in one flat directory, and ``macro_list`` is in the
``macros`` capability profile, so any agent that enables macros carries a tool
that can consume its whole context in one call.

The same defect was already fixed three times in this repo
(``browser_network_requests``, ``browser_websocket_messages``,
``browser_tail_recording``): a default limit, a cursor naming the first row not
returned, a response-size ceiling, and a non-positive limit that falls back to
the default rather than meaning unbounded. This is that pattern applied here.

What ``summary`` drops is measured rather than guessed. Across those 337
macros: ``description`` is **85.5%** of the payload, ``path`` 6.8% (and it is
derivable from the root plus the name), while ``parameters`` is 1.9% -- so
parameters stay, because an agent needs them to call ``macro_run`` at all.
"""

from __future__ import annotations

import json

import pytest

from octowright.macros.listing import (
    MACRO_LIST_DEFAULT_LIMIT,
    MACRO_LIST_MAX_LIMIT,
    MACRO_LIST_MAX_RESPONSE_CHARS,
    MACRO_LIST_SUMMARY_DESCRIPTION_CHARS,
    family_of,
    resolve_limit,
    select_macros,
)


def _entry(name: str, *, updated: str = "2026-08-01T00:00:00Z", description: str = "", actions: int = 3) -> dict:
    return {
        "name": name,
        "description": description or f"does {name}",
        "parameters": ["a", "b"],
        "path": f"/root/{name}.json",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": updated,
        "action_count": actions,
    }


def _corpus(count: int, prefix: str = "map") -> list[dict]:
    # Descending timestamps so the natural order is unambiguous.
    return [_entry(f"{prefix}-{i:03d}", updated=f"2026-08-{(count - i) % 28 + 1:02d}T00:00:00Z") for i in range(count)]


# ─── limits ──────────────────────────────────────────────────────────────────


def test_the_default_limit_applies_when_none_is_given() -> None:
    result = select_macros(_corpus(200))

    assert result["returned"] == MACRO_LIST_DEFAULT_LIMIT
    assert result["total"] == 200
    assert result["truncated"] is True


@pytest.mark.parametrize("limit", [0, -1, -1000])
def test_a_non_positive_limit_falls_back_to_the_default_not_to_unbounded(limit: int) -> None:
    """An LLM must not be able to remove the cap by passing 0."""
    result = select_macros(_corpus(200), limit=limit)

    assert result["returned"] == MACRO_LIST_DEFAULT_LIMIT


def test_a_limit_above_the_maximum_is_clamped() -> None:
    assert resolve_limit(10_000) == MACRO_LIST_MAX_LIMIT


def test_a_limit_below_the_maximum_is_honoured() -> None:
    result = select_macros(_corpus(200), limit=7)

    assert result["returned"] == 7


# ─── paging ──────────────────────────────────────────────────────────────────


def test_next_cursor_names_the_first_row_not_returned() -> None:
    entries = _corpus(10)
    first = select_macros(entries, limit=3)
    second = select_macros(entries, limit=3, cursor=first["next_cursor"])

    assert first["next_cursor"] == 3
    assert [m["name"] for m in first["macros"]] + [m["name"] for m in second["macros"]] == [
        m["name"] for m in select_macros(entries, limit=6)["macros"]
    ]


def test_the_last_page_is_not_truncated_and_has_no_cursor() -> None:
    result = select_macros(_corpus(5), limit=10)

    assert result["truncated"] is False
    assert result["next_cursor"] is None
    assert result["returned"] == 5


def test_a_negative_cursor_is_clamped_rather_than_wrapping() -> None:
    """cursor arrives as an LLM-supplied int; -1 would slice from the end."""
    result = select_macros(_corpus(5), limit=2, cursor=-4)

    assert [m["name"] for m in result["macros"]] == [m["name"] for m in select_macros(_corpus(5), limit=2)["macros"]]


def test_a_cursor_past_the_end_returns_nothing_and_stops() -> None:
    result = select_macros(_corpus(5), cursor=99)

    assert result["macros"] == []
    assert result["truncated"] is False
    assert result["next_cursor"] is None


def test_paging_order_is_stable_when_timestamps_tie() -> None:
    """Glob order is filesystem-dependent; a tie without a tiebreak lets a row
    move between calls, so a cursor could skip or repeat one."""
    tied = [_entry(name, updated="2026-08-01T00:00:00Z") for name in ("zebra", "alpha", "mongoose")]
    forward = [m["name"] for m in select_macros(tied)["macros"]]
    reshuffled = [m["name"] for m in select_macros(list(reversed(tied)))["macros"]]

    assert forward == reshuffled == ["alpha", "mongoose", "zebra"]


def test_newest_comes_first() -> None:
    entries = [
        _entry("old", updated="2026-01-01T00:00:00Z"),
        _entry("new", updated="2026-08-01T00:00:00Z"),
    ]

    assert [m["name"] for m in select_macros(entries)["macros"]] == ["new", "old"]


def test_a_macro_with_no_timestamp_sorts_last_rather_than_raising() -> None:
    entries = [_entry("dated", updated="2026-01-01T00:00:00Z"), {**_entry("undated"), "updated_at": None}]

    assert [m["name"] for m in select_macros(entries)["macros"]] == ["dated", "undated"]


# ─── filtering ───────────────────────────────────────────────────────────────


def test_prefix_selects_one_family() -> None:
    entries = _corpus(5, "map") + _corpus(3, "grant")
    result = select_macros(entries, prefix="map-")

    assert result["total"] == 5
    assert all(m["name"].startswith("map-") for m in result["macros"])


def test_contains_matches_anywhere_and_ignores_case() -> None:
    entries = [_entry("map-admin-import"), _entry("grant-ux-polish")]
    result = select_macros(entries, contains="ADMIN")

    assert [m["name"] for m in result["macros"]] == ["map-admin-import"]


def test_filters_combine() -> None:
    entries = [_entry("map-admin-import"), _entry("map-report-view"), _entry("grant-admin-x")]
    result = select_macros(entries, prefix="map-", contains="admin")

    assert [m["name"] for m in result["macros"]] == ["map-admin-import"]


def test_total_counts_the_filtered_set_not_the_corpus() -> None:
    """Otherwise a caller pages against a total that never matches what arrives."""
    entries = _corpus(100, "map") + _corpus(20, "grant")
    result = select_macros(entries, prefix="grant-", limit=5)

    assert result["total"] == 20


# ─── response modes ──────────────────────────────────────────────────────────


def test_summary_drops_the_two_fields_that_are_the_payload() -> None:
    long = "x" * (MACRO_LIST_SUMMARY_DESCRIPTION_CHARS + 500)
    result = select_macros([_entry("map-a", description=long)])
    row = result["macros"][0]

    assert "path" not in row
    assert len(row["description"]) <= MACRO_LIST_SUMMARY_DESCRIPTION_CHARS + 1
    assert row["description_truncated"] is True
    assert row["parameters"] == ["a", "b"], "parameters are 1.9% of the payload and required to call the macro"


def test_a_short_description_is_not_marked_truncated() -> None:
    row = select_macros([_entry("map-a", description="short")])["macros"][0]

    assert row["description"] == "short"
    assert row["description_truncated"] is False


def test_full_keeps_everything() -> None:
    long = "y" * (MACRO_LIST_SUMMARY_DESCRIPTION_CHARS + 500)
    row = select_macros([_entry("map-a", description=long)], response_mode="full")["macros"][0]

    assert row["description"] == long
    assert row["path"] == "/root/map-a.json"


def test_families_rolls_up_without_listing_anything() -> None:
    entries = _corpus(38, "map") + _corpus(24, "buyer") + [_entry("standalone")]
    result = select_macros(entries, response_mode="families")

    assert "macros" not in result
    families = {row["family"]: row for row in result["families"]}
    assert families["map"]["macros"] == 38
    assert families["buyer"]["macros"] == 24
    assert families["standalone"]["macros"] == 1
    assert result["total_macros"] == 63


def test_families_are_ordered_by_size() -> None:
    entries = _corpus(3, "small") + _corpus(9, "big")
    result = select_macros(entries, response_mode="families")

    assert [row["family"] for row in result["families"]] == ["big", "small"]


def test_families_carry_the_action_weight() -> None:
    """Count is the wrong ranking on its own -- one family held 39% of all actions."""
    entries = [_entry("grant-a", actions=700), _entry("grant-b", actions=700), _entry("map-a", actions=3)]
    result = select_macros(entries, response_mode="families")

    assert result["families"][0] == {
        "family": "grant",
        "macros": 2,
        "actions": 1400,
        "latest": "2026-08-01T00:00:00Z",
    }


def test_an_unknown_response_mode_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="response_mode"):
        select_macros(_corpus(3), response_mode="everything")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("map-admin-import", "map"),
        ("grant_ux_polish", "grant"),
        ("site.hao.smoke", "site"),
        ("standalone", "standalone"),
        ("", ""),
    ],
)
def test_family_is_the_first_token_of_the_name(name: str, expected: str) -> None:
    """The flat directory has no folders; the naming prefix is the only grouping."""
    assert family_of(name) == expected


# ─── response size ───────────────────────────────────────────────────────────


def test_a_row_cap_does_not_bound_size_so_the_char_budget_also_stops_the_page() -> None:
    fat = [_entry(f"map-{i:03d}", description="z" * 5_000) for i in range(200)]
    result = select_macros(fat, limit=MACRO_LIST_MAX_LIMIT, response_mode="full")

    assert result["returned"] < 200
    assert result["truncated"] is True
    assert len(json.dumps(result["macros"])) <= MACRO_LIST_MAX_RESPONSE_CHARS * 1.1


def test_one_oversized_row_is_still_returned() -> None:
    """Otherwise a caller pages forever on a row that can never fit."""
    huge = [_entry("map-huge", description="q" * (MACRO_LIST_MAX_RESPONSE_CHARS * 2))]
    result = select_macros(huge, response_mode="full")

    assert result["returned"] == 1


def test_the_root_is_reported_so_a_caller_knows_where_they_live() -> None:
    result = select_macros(_corpus(2), root="/home/tim/.config/octowright/macros")

    assert result["root"] == "/home/tim/.config/octowright/macros"
