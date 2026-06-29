# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Recording inspection tools for browser sessions."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from octowright.mcp_types import BrowserTailRecordingResult, BrowserToolAction
from octowright.recorder import tail_log
from octowright.server._state import mcp, pool
from octowright.server.profiles import annotate_next_actions_for_profile


def _count_items(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count} for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


@mcp.tool(
    structured_output=False,
    description=(
        "Read JSONL events appended to an instance's recording since byte offset `since`. "
        "Use this to STREAM events as they happen; use browser_recording_path if you just "
        "need the file path on disk. Pass the returned `cursor` back as `since` on the next "
        "call to read only new events (cursor pattern). When the file ends mid-line, the "
        "cursor stops at the start of the partial fragment so it will be re-read once "
        "completed; `complete` is True iff cursor == total_bytes. Pass max_events=N to "
        "bound raw event output, or response_mode='summary' for action counts and recent "
        "sanitized events without dumping the raw JSONL rows."
    ),
)
def browser_tail_recording(
    instance_id: str,
    since: int | None = None,
    max_events: int | None = None,
    response_mode: str | None = None,
    recent_limit: int = 8,
) -> BrowserTailRecordingResult:
    session = pool.get(instance_id)
    log_path = Path(session.log_path)
    prev = since or 0

    events, new_cursor, total_bytes = tail_log(log_path, prev)
    next_actions: list[BrowserToolAction] = []
    if response_mode == "summary":
        next_actions.append(
            {
                "tool": "browser_tail_recording",
                "args": {"instance_id": instance_id, "since": new_cursor, "response_mode": "summary"},
            }
        )
        next_actions.append(
            {"tool": "browser_tail_recording", "args": {"instance_id": instance_id, "since": new_cursor}}
        )
        capped_recent = max(0, min(int(recent_limit), 25))
        recent = events[-capped_recent:] if capped_recent else []
        return {
            "summary": {
                "event_count": len(events),
                "by_action": _count_items(Counter(str(event.get("action") or "unknown") for event in events)),
                "recent": [_recording_summary_event(event) for event in recent],
                "recent_limit": capped_recent,
            },
            "cursor": new_cursor,
            "total_bytes": total_bytes,
            "complete": new_cursor >= total_bytes,
            "next_actions": annotate_next_actions_for_profile(next_actions),
        }

    next_actions = [{"tool": "browser_tail_recording", "args": {"instance_id": instance_id, "since": new_cursor}}]

    returned_events = events
    truncated = False
    if max_events is not None:
        capped = max(0, int(max_events))
        returned_events = events[:capped]
        truncated = len(returned_events) < len(events)
        return {
            "events": returned_events,
            "cursor": new_cursor,
            "total_bytes": total_bytes,
            "complete": new_cursor >= total_bytes,
            "event_count": len(events),
            "returned_event_count": len(returned_events),
            "truncated": truncated,
            "next_actions": next_actions if truncated else [],
        }

    return {
        "events": returned_events,
        "cursor": new_cursor,
        "total_bytes": total_bytes,
        "complete": new_cursor >= total_bytes,
    }


def _recording_summary_event(event: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("action", "url", "selector", "target", "source", "role", "label", "text"):
        if event.get(key) is not None:
            out[key] = str(event[key])[:200]
    return out
