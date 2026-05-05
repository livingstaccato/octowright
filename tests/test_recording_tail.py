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


@pytest.fixture
def stub_pool(monkeypatch, tmp_path):
    log_path = tmp_path / "rec.jsonl"
    sessions = {"abc": SimpleNamespace(log_path=log_path)}
    fake_pool = SimpleNamespace(get=lambda iid: sessions[iid])
    monkeypatch.setattr(_inspect, "pool", fake_pool)
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


def test_partial_trailing_line(stub_pool):
    first = json.dumps({"action": "click", "n": 1}) + "\n"
    fragment = '{"action": "ty'
    stub_pool.write_text(first + fragment)

    result = _inspect.browser_tail_recording(instance_id="abc", since=0)
    assert result["events"] == [{"action": "click", "n": 1}]
    # Cursor should stop at the start of the partial line.
    assert result["cursor"] == len(first.encode("utf-8"))
    assert result["total_bytes"] == stub_pool.stat().st_size
    assert result["complete"] is False

    # Fragment is now completed; re-poll using returned cursor.
    completed = fragment + 'pe", "n": 2}\n'
    stub_pool.write_text(first + completed)
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
    stub_pool.write_text(line1 + line2)
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
