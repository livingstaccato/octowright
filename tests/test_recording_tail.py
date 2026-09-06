# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for ``browser_tail_recording`` MCP tool."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from octowright.recorder import tail_log
from octowright.server.browser import inspect as _inspect
from octowright.server.browser import inspect_recording as _inspect_recording


@pytest.fixture
def stub_pool(monkeypatch, tmp_path):
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    log_path = tmp_path / "rec.jsonl"
    sessions = {"abc": SimpleNamespace(log_path=log_path)}
    fake_pool = SimpleNamespace(get=lambda iid: sessions[iid])
    monkeypatch.setattr(_inspect, "pool", fake_pool)
    monkeypatch.setattr(_inspect_recording, "pool", fake_pool)
    return log_path


def test_missing_file(stub_pool):
    # File never created.
    assert not stub_pool.exists()
    result = _inspect.browser_tail_recording(instance_id="abc", since=0)
    assert result == {"events": [], "cursor": 0, "total_bytes": 0, "complete": True}

    result = _inspect.browser_tail_recording(instance_id="abc", since=42)
    assert result == {"events": [], "cursor": 42, "total_bytes": 0, "complete": True}


def test_empty_file(stub_pool):
    stub_pool.touch()
    result = _inspect.browser_tail_recording(instance_id="abc", since=0)
    assert result["events"] == []
    assert result["cursor"] == 0
    assert result["total_bytes"] == 0
    assert result["complete"] is True


def test_all_complete_lines(stub_pool):
    events = [{"action": "click", "n": 1}, {"action": "type", "n": 2}]
    stub_pool.write_text("".join(json.dumps(e) + "\n" for e in events))
    size = stub_pool.stat().st_size

    result = _inspect.browser_tail_recording(instance_id="abc", since=0)
    assert result["events"] == events
    assert result["cursor"] == size
    assert result["total_bytes"] == size
    assert result["complete"] is True


def test_tail_recording_summary_mode_bounds_payload_and_suggests_next_actions(stub_pool):
    events = [
        {"action": "navigate", "url": "https://example.com"},
        {"action": "click", "selector": "#a"},
        {"action": "type", "selector": "#q", "value": "secret@example.com"},
        {"action": "click", "selector": "#submit"},
    ]
    stub_pool.write_text("".join(json.dumps(e) + "\n" for e in events))

    result = _inspect.browser_tail_recording(instance_id="abc", since=0, response_mode="summary", recent_limit=2)

    assert "events" not in result
    assert result["summary"] == {
        # A short recording is read in one window, so the summary covers all of
        # it. `partial` is asserted here (not just in the truncated case) so the
        # honest-scope flag cannot be dropped without a test noticing.
        "partial": False,
        "event_count": 4,
        "by_action": [
            {"key": "click", "count": 2},
            {"key": "navigate", "count": 1},
            {"key": "type", "count": 1},
        ],
        "recent": [
            {"action": "type", "selector": "#q"},
            {"action": "click", "selector": "#submit"},
        ],
        "recent_limit": 2,
    }
    assert result["next_actions"] == [
        {
            "tool": "browser_tail_recording",
            "args": {"instance_id": "abc", "since": result["cursor"], "response_mode": "summary"},
        },
        {"tool": "browser_tail_recording", "args": {"instance_id": "abc", "since": result["cursor"]}},
    ]


def test_tail_recording_raw_mode_can_limit_events(stub_pool):
    events = [{"action": "click", "n": n} for n in range(5)]
    stub_pool.write_text("".join(json.dumps(e) + "\n" for e in events))

    result = _inspect.browser_tail_recording(instance_id="abc", since=0, max_events=2)

    assert result["events"] == events[:2]
    assert result["event_count"] == 5
    assert result["returned_event_count"] == 2
    assert result["truncated"] is True
    assert result["next_actions"] == [
        {"tool": "browser_tail_recording", "args": {"instance_id": "abc", "since": result["cursor"]}},
    ]


def test_partial_trailing_line(stub_pool):
    first = json.dumps({"action": "click", "n": 1}) + "\n"
    fragment = '{"action": "ty'
    stub_pool.write_bytes((first + fragment).encode("utf-8"))

    result = _inspect.browser_tail_recording(instance_id="abc", since=0)
    assert result["events"] == [{"action": "click", "n": 1}]
    # Cursor should stop at the start of the partial line.
    assert result["cursor"] == len(first.encode("utf-8"))
    assert result["total_bytes"] == stub_pool.stat().st_size
    assert result["complete"] is False

    # Fragment is now completed; re-poll using returned cursor.
    completed = fragment + 'pe", "n": 2}\n'
    stub_pool.write_bytes((first + completed).encode("utf-8"))
    result2 = _inspect.browser_tail_recording(instance_id="abc", since=result["cursor"])
    assert result2["events"] == [{"action": "type", "n": 2}]
    assert result2["cursor"] == stub_pool.stat().st_size
    assert result2["complete"] is True


def test_malformed_json_skipped(stub_pool):
    body = (
        json.dumps({"action": "click", "n": 1})
        + "\n"
        + "{not json at all\n"
        + json.dumps({"action": "type", "n": 2})
        + "\n"
    )
    stub_pool.write_text(body)
    result = _inspect.browser_tail_recording(instance_id="abc", since=0)
    assert result["events"] == [{"action": "click", "n": 1}, {"action": "type", "n": 2}]
    assert result["complete"] is True


def test_since_offset_skips_earlier_events(stub_pool):
    line1 = json.dumps({"action": "click", "n": 1}) + "\n"
    line2 = json.dumps({"action": "type", "n": 2}) + "\n"
    stub_pool.write_bytes((line1 + line2).encode("utf-8"))
    offset = len(line1.encode("utf-8"))

    result = _inspect.browser_tail_recording(instance_id="abc", since=offset)
    assert result["events"] == [{"action": "type", "n": 2}]
    assert result["cursor"] == stub_pool.stat().st_size
    assert result["complete"] is True


def test_tail_log_keeps_cursor_before_incomplete_utf8_line(tmp_path):
    path = tmp_path / "rec.jsonl"
    full_line = json.dumps({"action": "click"}, ensure_ascii=False).encode("utf-8") + b"\n"
    partial_line = json.dumps({"action": "type", "text": "snow "}, ensure_ascii=False).encode("utf-8")[:-2]
    partial_line += "☃".encode()[:2]
    path.write_bytes(full_line + partial_line)

    events, cursor, total_bytes = tail_log(path, 0)

    assert events == [{"action": "click"}]
    assert cursor == len(full_line)
    assert total_bytes == path.stat().st_size


def test_tail_log_replays_completed_multibyte_line_from_saved_cursor(tmp_path):
    path = tmp_path / "rec.jsonl"
    first = json.dumps({"action": "click"}, ensure_ascii=False).encode("utf-8") + b"\n"
    second = json.dumps({"action": "type", "text": "snow ☃"}, ensure_ascii=False).encode("utf-8") + b"\n"
    split_at = len(first) + len(second) - 2
    path.write_bytes((first + second)[:split_at])

    events, cursor, _ = tail_log(path, 0)
    assert events == [{"action": "click"}]
    assert cursor == len(first)

    path.write_bytes(first + second)
    events2, cursor2, _ = tail_log(path, cursor)
    assert events2 == [{"action": "type", "text": "snow ☃"}]
    assert cursor2 == path.stat().st_size


def test_capped_paging_reaches_every_event(stub_pool):
    """`cursor` must name the first event NOT returned.

    It named the end of the whole read window instead, so a caller following
    the tool's own `next_actions` — which hand back exactly this cursor when
    `truncated` — skipped every event the cap had left behind. Two at a time
    through five events returned 0-1 and then nothing.
    """
    events = [{"action": "click", "n": n} for n in range(5)]
    stub_pool.write_text("".join(json.dumps(e) + "\n" for e in events))

    seen = []
    cursor = 0
    for _ in range(10):
        result = _inspect.browser_tail_recording(instance_id="abc", since=cursor, max_events=2)
        seen.extend(result["events"])
        cursor = result["cursor"]
        if not result["truncated"]:
            break

    assert seen == events


def test_a_capped_read_is_never_reported_complete(stub_pool):
    """`complete` and `truncated` were both True on the same response.

    They cannot both hold: `complete` says the cursor reached the end of the
    file, and `truncated` says events were left behind for the next call.
    """
    events = [{"action": "click", "n": n} for n in range(5)]
    stub_pool.write_text("".join(json.dumps(e) + "\n" for e in events))

    result = _inspect.browser_tail_recording(instance_id="abc", since=0, max_events=2)

    assert result["truncated"] is True
    assert result["complete"] is False


def test_a_zero_cap_still_advances_the_cursor(stub_pool):
    """`max_events=0` must not become a non-advancing loop.

    Returning no events while reporting `truncated` and handing back the
    SAME cursor tells a caller following `next_actions` to ask again forever.
    A bound of zero is meaningless, so it is floored at one -- the same rule
    the websocket byte budget uses to guarantee a page always makes progress.
    """
    events = [{"action": "click", "n": n} for n in range(3)]
    stub_pool.write_text("".join(json.dumps(e) + "\n" for e in events))

    result = _inspect.browser_tail_recording(instance_id="abc", since=0, max_events=0)

    assert result["returned_event_count"] >= 1
    assert result["cursor"] > 0


def test_a_cut_read_window_is_reported_as_truncated(stub_pool, monkeypatch):
    """`truncated` reported only the row cap, so a cut window read as complete.

    The same defect `read_frames` was fixed for in this area: with the byte
    window ending the page first, the tool returned every event it had, said
    `truncated: false`, and offered no next action -- and the description tells
    the caller that `truncated` is the signal to keep going.
    """
    monkeypatch.setenv("OCTOWRIGHT_TAIL_MAX_BYTES", "300")
    events = [{"action": "click", "n": n} for n in range(50)]
    stub_pool.write_text("".join(json.dumps(e) + "\n" for e in events))

    result = _inspect.browser_tail_recording(instance_id="abc", since=0, max_events=100)

    assert result["returned_event_count"] < 50
    assert result["truncated"] is True
    assert result["next_actions"]


def test_a_partial_trailing_line_terminates_the_page_loop(stub_pool):
    """The mirror: a held cursor must stop asking for another call.

    The first call cannot know the remaining bytes are half a line, so it may
    ask once more; the call that finds the cursor frozen must not.
    """
    first = json.dumps({"action": "click", "n": 1}) + "\n"
    stub_pool.write_bytes((first + '{"action": "ty').encode("utf-8"))

    calls = 0
    result = _inspect.browser_tail_recording(instance_id="abc", since=0, max_events=100)
    while result["truncated"] and calls < 10:
        calls += 1
        result = _inspect.browser_tail_recording(instance_id="abc", since=result["cursor"], max_events=100)

    assert calls <= 1
    assert result["next_actions"] == []
