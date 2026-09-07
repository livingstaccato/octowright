# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The shared bounded-listing core behind every list-returning MCP tool.

`macro_list` was unbounded and returned 402,942 characters from a real
337-macro directory. An AST scan afterwards found four more tools with the
same shape -- `golden_list`, `persona_list`, `profile_list`, `scenario_list`
-- so the fix belongs in one place rather than copied five times.

Sizes were measured before choosing the shape: goldens are 265 chars/row over
69 rows and personas 216 chars/row over 5, with no field dominating the way a
macro's `description` does (85.5%). So these get a limit, a cursor and a name
filter, and deliberately NOT the summary/full modes `macro_list` needs --
machinery without evidence is how a compact mode ends up dropping the one
field a caller required.
"""

from __future__ import annotations

import pytest

from octowright.listing import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_RESPONSE_CHARS,
    match_name,
    order_newest_first,
    paginate,
    resolve_limit,
)


def _rows(count: int, prefix: str = "row") -> list[dict]:
    return [{"name": f"{prefix}-{i:03d}", "updated_at": f"2026-08-{(count - i) % 28 + 1:02d}"} for i in range(count)]


# ─── limits ──────────────────────────────────────────────────────────────────


def test_the_default_limit_applies() -> None:
    assert paginate(_rows(200))["returned"] == DEFAULT_LIMIT


@pytest.mark.parametrize("limit", [0, -1, -999])
def test_a_non_positive_limit_falls_back_to_the_default(limit: int) -> None:
    """`limit=0` reads as "no limit" in many APIs; here that is the bug."""
    assert resolve_limit(limit) == DEFAULT_LIMIT


def test_none_is_the_default_too() -> None:
    assert resolve_limit(None) == DEFAULT_LIMIT


def test_a_limit_above_the_maximum_is_clamped() -> None:
    assert resolve_limit(10_000) == MAX_LIMIT


# ─── paging ──────────────────────────────────────────────────────────────────


def test_next_cursor_names_the_first_row_not_returned() -> None:
    rows = _rows(10)
    first = paginate(rows, limit=3)
    assert first["next_cursor"] == 3
    assert first["truncated"] is True

    second = paginate(rows, limit=3, cursor=first["next_cursor"])
    assert [r["name"] for r in second["items"]] == [r["name"] for r in rows[3:6]]


def test_the_last_page_reports_no_more() -> None:
    result = paginate(_rows(4), limit=10)
    assert (result["truncated"], result["next_cursor"], result["returned"]) == (False, None, 4)


def test_a_negative_cursor_is_clamped_rather_than_slicing_from_the_end() -> None:
    rows = _rows(5)
    assert paginate(rows, limit=2, cursor=-3)["items"] == paginate(rows, limit=2)["items"]


def test_a_cursor_past_the_end_yields_an_empty_final_page() -> None:
    result = paginate(_rows(5), cursor=99)
    assert (result["items"], result["truncated"], result["next_cursor"]) == ([], False, None)


def test_total_is_the_size_of_what_was_passed_in() -> None:
    assert paginate(_rows(120), limit=5)["total"] == 120


# ─── response size ───────────────────────────────────────────────────────────


def test_the_char_budget_stops_a_page_a_row_cap_would_not() -> None:
    """A row cap does not bound size."""
    fat = [{"name": f"r{i}", "blob": "z" * 5_000} for i in range(300)]
    result = paginate(fat, limit=MAX_LIMIT)
    assert result["returned"] < 300
    assert result["truncated"] is True


def test_one_oversized_row_is_still_returned() -> None:
    """Otherwise a caller pages forever on a row that can never fit."""
    huge = [{"name": "r", "blob": "q" * (MAX_RESPONSE_CHARS * 2)}]
    assert paginate(huge)["returned"] == 1


# ─── ordering and filtering ──────────────────────────────────────────────────


def test_ordering_is_newest_first_with_a_name_tiebreak() -> None:
    """Without the tiebreak a cursor can skip and repeat rows between calls,
    because the underlying listings are built from filesystem order."""
    tied = [
        {"name": "zebra", "updated_at": "2026-08-01"},
        {"name": "alpha", "updated_at": "2026-08-01"},
        {"name": "newer", "updated_at": "2026-09-01"},
    ]
    ordered = order_newest_first(tied, time_key="updated_at")
    assert [r["name"] for r in ordered] == ["newer", "alpha", "zebra"]
    assert [r["name"] for r in order_newest_first(list(reversed(tied)), time_key="updated_at")] == [
        "newer",
        "alpha",
        "zebra",
    ]


def test_a_missing_timestamp_sorts_last_rather_than_raising() -> None:
    rows = [{"name": "dated", "updated_at": "2026-01-01"}, {"name": "undated"}]
    assert [r["name"] for r in order_newest_first(rows, time_key="updated_at")] == ["dated", "undated"]


def test_an_alternate_time_key_is_honoured() -> None:
    """personas sort on `mtime`, goldens on `updated_at`."""
    rows = [{"name": "a", "mtime": 1}, {"name": "b", "mtime": 9}]
    assert [r["name"] for r in order_newest_first(rows, time_key="mtime")] == ["b", "a"]


def test_prefix_and_contains_filter_by_name() -> None:
    rows = [{"name": "map-admin"}, {"name": "map-report"}, {"name": "grant-admin"}]
    assert [r["name"] for r in match_name(rows, prefix="map-")] == ["map-admin", "map-report"]
    assert [r["name"] for r in match_name(rows, contains="ADMIN")] == ["map-admin", "grant-admin"]
    assert [r["name"] for r in match_name(rows, prefix="map-", contains="admin")] == ["map-admin"]


def test_no_filter_returns_everything() -> None:
    rows = [{"name": "a"}, {"name": "b"}]
    assert match_name(rows) == rows


def test_a_row_with_no_name_is_filtered_out_rather_than_crashing() -> None:
    rows = [{"name": "map-a"}, {}]
    assert [r["name"] for r in match_name(rows, prefix="map-")] == ["map-a"]
