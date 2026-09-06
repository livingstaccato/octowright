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
from octowright.recorder import parse_log_line, tail_log, tail_log_lines
from octowright.server._state import mcp, pool
from octowright.server.profiles import annotate_next_actions_for_profile


def _capped_events(log_path: Path, since: int, cap: int) -> tuple[list[dict[str, Any]], int, int, int]:
    """``(events, events_in_window, cursor, total_bytes)`` for a capped read.

    ``cursor`` is the byte offset of the first event NOT returned, which is
    the only value a caller can resume from without losing the ones the cap
    left behind. Reading the parsed window and reporting its END cursor -- as
    this did -- silently dropped them, and the tool's own ``next_actions``
    hand that cursor straight back when ``truncated``, so the loss was
    instructed rather than merely possible.

    The whole window is still scanned, so ``events_in_window`` stays an exact
    count rather than becoming "however many we happened to parse". That
    deliberately forgoes the laziness ``tail_log_lines`` exists for: stopping
    at ``cap + 1`` would be cheaper on a large recording, but ``event_count``
    is a published field meaning "events in this window", and quietly
    redefining it as "events we bothered to count" is a worse trade than the
    parse. Note it also means this path has no response-BYTE budget, unlike
    the websocket reader -- a recording holding enormous rows can still return
    a large response here. Bounding that is a change to this tool's contract
    rather than a fix to this one, and is not attempted.
    """
    events: list[dict[str, Any]] = []
    in_window = 0
    stopped_at: int | None = None
    lines, window_cursor, total_bytes = tail_log_lines(log_path, since)
    for offset, raw in lines:
        event = parse_log_line(raw)
        if event is None:
            continue
        in_window += 1
        if len(events) < cap:
            events.append(event)
        elif stopped_at is None:
            stopped_at = offset
    return events, in_window, window_cursor if stopped_at is None else stopped_at, total_bytes


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
        "bound raw event output — the cursor then points at the first event NOT returned, so "
        "resuming from it loses nothing and `truncated` tells you to keep going. Or use "
        "response_mode='summary' for action counts and recent "
        "sanitized events without dumping the raw JSONL rows. A summary describes the "
        "bytes scanned by that ONE call, not the whole file — check summary.partial "
        "and keep resuming from `cursor` until it is false before reporting totals."
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

    if max_events is not None:
        return _capped_result(instance_id, log_path, prev, max_events)

    # Read ONLY on the paths that use it: the capped path re-reads and
    # re-parses the same window through ``_capped_events``, and doing both
    # opened the file twice and held two complete lists of parsed rows in the
    # process that owns every live browser -- measured at 346ms for five
    # events out of a 4.6 MB recording.
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
                # Counts describe the bytes scanned by THIS call, not the whole
                # recording: tail_log reads a bounded window (0.15.0,
                # OCTOWRIGHT_TAIL_MAX_BYTES) so one `since=0` on a long-lived
                # session cannot pull gigabytes into the leader. `partial` sits
                # beside the counts rather than only at the top level, because a
                # reader that sees `event_count` without it reports a prefix as
                # the total. Resume from `cursor` until `partial` is false.
                "partial": new_cursor < total_bytes,
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

    return {
        "events": events,
        "cursor": new_cursor,
        "total_bytes": total_bytes,
        "complete": new_cursor >= total_bytes,
    }


def _capped_result(instance_id: str, log_path: Path, prev: int, max_events: int) -> BrowserTailRecordingResult:
    """The ``max_events`` branch: bounded rows, and a cursor that resumes on one."""
    # Floored at one, not zero. A zero cap returns nothing while the corrected
    # cursor correctly refuses to advance past events it did not return -- so
    # `truncated` stays true and `next_actions` hands back the same cursor,
    # telling a caller to ask again forever. A bound of zero is meaningless; a
    # page that always makes progress is the rule the websocket byte budget
    # uses for the same reason.
    events, in_window, cursor, total_bytes = _capped_events(log_path, prev, max(1, int(max_events)))
    # "Page again and you will get more." The row cap left events behind, or
    # the byte window cut the file -- but the second only counts when the
    # cursor actually moved, since ``_read_window`` holds it on an unterminated
    # trailing line and a caller following `next_actions` would loop on it.
    truncated = len(events) < in_window or (cursor > prev and cursor < total_bytes)
    next_actions: list[BrowserToolAction] = [
        {"tool": "browser_tail_recording", "args": {"instance_id": instance_id, "since": cursor}}
    ]
    return {
        "events": events,
        "cursor": cursor,
        "total_bytes": total_bytes,
        # Follows the corrected cursor, so it can no longer be True on the
        # same response as ``truncated`` -- a pair that cannot both hold, and did.
        "complete": cursor >= total_bytes,
        "event_count": in_window,
        "returned_event_count": len(events),
        "truncated": truncated,
        "next_actions": next_actions if truncated else [],
    }


def _recording_summary_event(event: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("action", "url", "selector", "target", "source", "role", "label", "text"):
        if event.get(key) is not None:
            out[key] = str(event[key])[:200]
    return out
