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

from collections.abc import Mapping, Sequence
from typing import Any, Final

from octowright.listing import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_RESPONSE_CHARS,
    match_name,
    order_newest_first,
    paginate,
    resolve_limit,
)

# Re-exported under the macro-specific names this module's callers and tests
# already use; the values and the rules behind them live in octowright.listing,
# shared with golden_list/persona_list/profile_list/scenario_list.
MACRO_LIST_DEFAULT_LIMIT: Final = DEFAULT_LIMIT
MACRO_LIST_MAX_LIMIT: Final = MAX_LIMIT
MACRO_LIST_MAX_RESPONSE_CHARS: Final = MAX_RESPONSE_CHARS

# Enough to tell two macros apart; the average saved description is ~844
# characters, which is the 85.5% this exists to cut.
MACRO_LIST_SUMMARY_DESCRIPTION_CHARS: Final = 120

RESPONSE_MODES: Final = ("summary", "full", "families")

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
    matching = order_newest_first(match_name(entries, prefix=prefix, contains=contains))

    if response_mode == "families":
        families = _families(matching, resolved_limit)
        return {
            "families": families,
            "total_macros": len(matching),
            "total_families": len({family_of(str(e.get("name") or "")) for e in matching}),
            "response_mode": response_mode,
            "root": root,
        }

    page = paginate(
        matching,
        limit=resolved_limit,
        cursor=cursor,
        # `dict` copies: handing back the stored mapping would let one caller's
        # in-place edit rewrite what every later reader sees, the defect
        # _select_console_tail and the websocket registry each had.
        build_row=dict if response_mode == "full" else _summary_row,
    )
    return {
        "macros": page["items"],
        "total": page["total"],
        "returned": page["returned"],
        "truncated": page["truncated"],
        "next_cursor": page["next_cursor"],
        "response_mode": response_mode,
        "root": root,
    }
