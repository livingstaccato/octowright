# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A small bounded ring of recent stability incidents (renderer crashes, driver
restarts) so ``octowright_status`` can answer *what happened*, not just *how many*.

OTel counters are write-only in-process; this keeps the last few incidents with
enough context (instance, url, outcome, timestamp) that an operator or the LLM
can articulate "browser X at <url> crashed at <ts> and recovered" from a single
status call instead of grepping the daemon log. Bounded by ``_RING_SIZE`` so a
long-lived daemon can't grow it without limit.
"""

from __future__ import annotations

import os
from collections import deque
from datetime import UTC, datetime
from typing import Any

# Categories recorded today. Kept as plain strings (not an enum) so callers in
# different modules don't need a shared import beyond this one.
CATEGORY_RENDERER_CRASH = "renderer_crash"
CATEGORY_DRIVER_RESTART = "driver_restart"
# A browser session lost when the shared driver died (H4a). Recorded with
# outcome="relaunched" + new_instance_id when auto-relaunch reopens it.
CATEGORY_DRIVER_LOST = "driver_lost"
# A target that stopped answering a Playwright call within its budget
# (SessionCallTimeoutError). No Playwright event reports this -- it is
# recorded from session/timeouts.py's call budget rather than observed like
# a renderer crash, and it has no crash report to correlate.
CATEGORY_UNRESPONSIVE_TARGET = "unresponsive_target"

_RING_SIZE = int(os.environ.get("OCTOWRIGHT_INCIDENT_RING_SIZE", "25"))
_RING: deque[dict[str, Any]] = deque(maxlen=_RING_SIZE)


def _rebuild_ring() -> None:
    """Rebuild the deque after ``_RING_SIZE`` is changed (tests)."""
    global _RING
    _RING = deque(_RING, maxlen=_RING_SIZE)


def record(category: str, **fields: Any) -> dict[str, Any]:
    """Append an incident and return the stored dict (mutable — callers may
    update ``outcome`` in place as an async recovery resolves)."""
    rec: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "category": category,
        **fields,
    }
    _RING.append(rec)
    return rec


def recent(*, category: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """Recent incidents oldest→newest, optionally filtered by category and capped
    to the newest ``limit``."""
    items = [r for r in _RING if category is None or r.get("category") == category]
    return items[-limit:] if limit is not None else items


def counts(*, category: str | None = None) -> dict[str, int]:
    """Tally of ``outcome`` values across the retained incidents (optionally one
    category). Reflects only what's still in the ring, so it's a recent-window
    view, not a lifetime total."""
    out: dict[str, int] = {}
    for r in recent(category=category):
        outcome = r.get("outcome")
        if outcome is not None:
            out[outcome] = out.get(outcome, 0) + 1
    return out


def reset() -> None:
    """Clear the ring (tests / operator process access)."""
    _RING.clear()
