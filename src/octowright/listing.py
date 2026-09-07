# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Bounded paging shared by every list-returning MCP tool.

``macro_list`` returned every saved macro with no bound of any kind: measured
at **402,942 characters -- roughly 100k tokens -- in one call** from a real
337-macro directory, in a tool that ships in a capability profile. An AST scan
afterwards found four more tools with the same shape (``golden_list``,
``persona_list``, ``profile_list``, ``scenario_list``), which is why the rule
lives here rather than being copied five times and drifting.

The shape matches what ``browser_network_requests``,
``browser_websocket_messages`` and ``browser_tail_recording`` already settled
on:

* a default row limit, clamped to a maximum;
* a ``next_cursor`` naming the first row **not** returned, so a caller that
  pages gets a prefix rather than a hole;
* a response-size ceiling **on top of** the row cap, because a row cap does not
  bound size;
* a non-positive limit resolving to the DEFAULT rather than to unbounded -- an
  LLM must not be able to remove the cap by passing ``0``.

This module deliberately offers no summary/full modes. Row sizes were measured
before choosing: goldens are ~265 chars/row and personas ~216, with no field
dominating the way a macro's ``description`` does (85.5% of its payload). A
compact mode there would be machinery without evidence, and the way that goes
wrong is dropping the one field a caller needed. ``macros/listing.py`` builds
its macro-specific modes on top of these primitives.

Lives at the package root, like ``dashboard_events`` and ``console_levels``, so
``server/`` tool modules share it without reaching into each other's packages.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 500

# Sized well above a default page of any current listing, so an ordinary call
# never meets it and only a pathological row can.
MAX_RESPONSE_CHARS: Final = 60_000

# Covers the JSON structure each row's strings sit in, so many tiny rows cannot
# walk past the budget.
ROW_OVERHEAD_CHARS: Final = 24


def resolve_limit(limit: int | None) -> int:
    """Rows per call: the default when unset or non-positive, clamped to the max."""
    if limit is None or limit <= 0:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


def order_newest_first(
    rows: Sequence[Mapping[str, Any]],
    *,
    time_key: str = "updated_at",
    name_key: str = "name",
) -> list[Mapping[str, Any]]:
    """Newest first, ties broken by name.

    The tiebreak is load-bearing rather than tidy. These listings are built from
    directory globs, whose order is filesystem-dependent, so rows sharing a
    timestamp otherwise hold an arbitrary relative order that can differ between
    two calls -- and a cursor into a list that reorders under it silently skips
    some rows and repeats others.

    Two passes rather than one composite key: a single ``reverse=True`` would
    reverse the name tiebreak too. Python's sort is stable, so sorting by name
    and then by time descending leaves equal-time rows in ascending name order.
    """
    by_name = sorted(rows, key=lambda row: str(row.get(name_key) or ""))
    return sorted(by_name, key=lambda row: str(row.get(time_key) or ""), reverse=True)


def match_name(
    rows: Sequence[Mapping[str, Any]],
    *,
    prefix: str | None = None,
    contains: str | None = None,
    name_key: str = "name",
) -> list[Mapping[str, Any]]:
    """Rows whose name starts with *prefix* and contains *contains* (case-insensitive)."""
    if not prefix and not contains:
        return list(rows)
    out: list[Mapping[str, Any]] = []
    for row in rows:
        name = str(row.get(name_key) or "")
        if prefix and not name.startswith(prefix):
            continue
        if contains and contains.lower() not in name.lower():
            continue
        out.append(row)
    return out


def paginate(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int | None = None,
    cursor: int = 0,
    build_row: Any = None,
) -> dict[str, Any]:
    """One bounded page of *rows*, plus the cursor to continue from.

    ``build_row`` optionally reshapes each returned row (a caller with a compact
    mode); the budget is measured on what it produces, since that is what
    crosses the transport.
    """
    resolved = resolve_limit(limit)
    # Negative arrives from an LLM-supplied int and would slice from the end.
    start = max(0, cursor)
    items: list[Mapping[str, Any]] = []
    budget = MAX_RESPONSE_CHARS
    index = start
    for row in rows[start : start + resolved]:
        shaped = build_row(row) if build_row is not None else row
        cost = len(json.dumps(shaped, default=str)) + ROW_OVERHEAD_CHARS
        # The first row is returned even when it alone exceeds the budget, or a
        # caller pages forever on a row that can never fit.
        if items and cost > budget:
            break
        budget -= cost
        items.append(shaped)
        index += 1
    more = index < len(rows)
    return {
        "items": items,
        "total": len(rows),
        "returned": len(items),
        "truncated": more,
        "next_cursor": index if more else None,
    }
