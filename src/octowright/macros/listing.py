# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Bounded, filterable selection over the saved-macro inventory.

``macro_list`` returned every saved macro with every description and no bound
of any kind. Measured on a real machine: **402,942 characters -- roughly 100k
tokens -- in one call**, from 337 macros in one flat directory. The tool is in
the ``macros`` capability profile, so any agent that enables macros carries a
tool able to consume its entire context in a single call.

This is the same defect ``browser_network_requests``,
``browser_websocket_messages`` and ``browser_tail_recording`` each already
fixed, and it is deliberately the same shape: a default limit, a cursor naming
the first row NOT returned, a response-size ceiling on top of the row cap
(a row cap does not bound size), and a non-positive limit that resolves to the
default rather than meaning unbounded -- an LLM must not be able to remove the
cap by passing ``0``.

**What ``summary`` drops is measured, not guessed.** Across those 337 macros,
``description`` is 85.5% of the payload and ``path`` a further 6.8% (and the
path is derivable from the root plus the name), while ``parameters`` is 1.9%.
So descriptions are capped and paths omitted, and parameters stay -- an agent
that cannot see a macro's parameters cannot call it, which would make the
compact mode useless and send every caller to ``full``.

**Ordering is (updated_at desc, name asc), and the tiebreak is load-bearing.**
``storage.list_macros`` builds its list from ``Path.glob``, whose order is
filesystem-dependent, and sorts on ``updated_at`` alone. Macros saved in the
same second therefore hold an arbitrary relative order that can differ between
two calls -- and a cursor into a list that reorders under it silently skips
some rows and repeats others.

The pure selection lives here rather than in ``server/macros.py`` so it is
testable without the MCP layer, matching how ``macros/semantic.py`` holds the
helpers behind the ``macro_explain`` tool.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

# Chosen so an ordinary listing is a few thousand characters. Paging is one
# extra round trip; an unbounded first response is a lost conversation.
MACRO_LIST_DEFAULT_LIMIT: Final = 50
MACRO_LIST_MAX_LIMIT: Final = 500

# Ceiling on the rows a single call may serialize. Sized well above a default
# summary page, so an ordinary call never meets it.
MACRO_LIST_MAX_RESPONSE_CHARS: Final = 60_000

# Enough to tell two macros apart; the average saved description is ~844
# characters, which is the 85.5% this exists to cut.
MACRO_LIST_SUMMARY_DESCRIPTION_CHARS: Final = 120

RESPONSE_MODES: Final = ("summary", "full", "families")

# A per-row constant covering the JSON structure the row's own strings sit in,
# so the budget cannot be walked past by many tiny rows.
_ROW_OVERHEAD_CHARS: Final = 24

_FAMILY_SEPARATORS: Final = ("-", "_", ".")


def family_of(name: str) -> str:
    """The grouping token of a macro name.

    The macro directory is flat -- no subdirectories exist -- so a naming
    prefix (``map-``, ``grant-``, ``admin-``) is the only grouping anyone has.
    """
    for separator in _FAMILY_SEPARATORS:
        if separator in name:
            return name.split(separator, 1)[0]
    return name


def resolve_limit(limit: int | None) -> int:
    """Rows per call: default when unset or non-positive, clamped to the max.

    Non-positive resolving to the DEFAULT (never to unbounded) is the point:
    ``limit=0`` reads as "no limit" in many APIs, and here that is exactly the
    response this module exists to prevent.
    """
    if limit is None or limit <= 0:
        return MACRO_LIST_DEFAULT_LIMIT
    return min(limit, MACRO_LIST_MAX_LIMIT)


def _ordered(entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Newest first, ties broken by name.

    Two passes rather than one composite key: a single ``reverse=True`` sort
    would reverse the NAME tiebreak as well as the date. Python's sort is
    stable, so sorting by name and then by date descending leaves same-second
    macros in ascending name order.
    """
    by_name = sorted(entries, key=lambda entry: str(entry.get("name") or ""))
    return sorted(by_name, key=lambda entry: str(entry.get("updated_at") or ""), reverse=True)


def _matches(entry: Mapping[str, Any], prefix: str | None, contains: str | None) -> bool:
    name = str(entry.get("name") or "")
    if prefix and not name.startswith(prefix):
        return False
    return not (contains and contains.lower() not in name.lower())


def _summary_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    description = str(entry.get("description") or "")
    truncated = len(description) > MACRO_LIST_SUMMARY_DESCRIPTION_CHARS
    return {
        "name": entry.get("name"),
        "description": description[:MACRO_LIST_SUMMARY_DESCRIPTION_CHARS] + ("…" if truncated else ""),
        "description_truncated": truncated,
        "parameters": entry.get("parameters", []),
        "updated_at": entry.get("updated_at"),
        "action_count": entry.get("action_count"),
    }


def _families(entries: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = family_of(str(entry.get("name") or ""))
        row = grouped.setdefault(key, {"family": key, "macros": 0, "actions": 0, "latest": ""})
        row["macros"] += 1
        row["actions"] += int(entry.get("action_count") or 0)
        updated = str(entry.get("updated_at") or "")
        if updated > str(row["latest"]):
            row["latest"] = updated
    # By macro count, then by action weight: count alone is the wrong ranking
    # when one family holds a third of every action ever recorded.
    ordered = sorted(grouped.values(), key=lambda row: (-int(row["macros"]), -int(row["actions"]), row["family"]))
    return ordered[:limit]


def _page_rows(
    matching: Sequence[Mapping[str, Any]],
    *,
    start: int,
    limit: int,
    response_mode: str,
) -> tuple[list[dict[str, Any]], int]:
    """One page of rows, and the absolute index of the first row NOT returned.

    Two bounds apply together, because a row cap does not bound size: at most
    ``limit`` rows, and at most ``MACRO_LIST_MAX_RESPONSE_CHARS`` of serialized
    row content.
    """
    rows: list[dict[str, Any]] = []
    budget = MACRO_LIST_MAX_RESPONSE_CHARS
    index = start
    for entry in matching[start : start + limit]:
        row = dict(entry) if response_mode == "full" else _summary_row(entry)
        cost = len(json.dumps(row, default=str)) + _ROW_OVERHEAD_CHARS
        # The first row is returned even when it alone exceeds the budget, or a
        # caller pages forever on a row that can never fit.
        if rows and cost > budget:
            break
        budget -= cost
        rows.append(row)
        index += 1
    return rows, index


def select_macros(
    entries: Sequence[Mapping[str, Any]],
    *,
    prefix: str | None = None,
    contains: str | None = None,
    limit: int | None = None,
    cursor: int = 0,
    response_mode: str = "summary",
    root: str | None = None,
) -> dict[str, Any]:
    """One bounded page of the macro inventory, or a roll-up of its families."""
    if response_mode not in RESPONSE_MODES:
        raise ValueError(f"response_mode must be one of {', '.join(RESPONSE_MODES)}; got {response_mode!r}")

    resolved_limit = resolve_limit(limit)
    matching = _ordered([entry for entry in entries if _matches(entry, prefix, contains)])

    if response_mode == "families":
        families = _families(matching, resolved_limit)
        return {
            "families": families,
            "total_macros": len(matching),
            "total_families": len({family_of(str(e.get("name") or "")) for e in matching}),
            "response_mode": response_mode,
            "root": root,
        }

    # Negative arrives from an LLM-supplied int and would slice from the end.
    start = max(0, cursor)
    rows, index = _page_rows(matching, start=start, limit=resolved_limit, response_mode=response_mode)
    more = index < len(matching)
    return {
        "macros": rows,
        "total": len(matching),
        "returned": len(rows),
        "truncated": more,
        "next_cursor": index if more else None,
        "response_mode": response_mode,
        "root": root,
    }
