# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.recorder.

Pins:
- Recorder.__init__ creates parent dirs and opens the file in append mode
- record() writes one JSONL line with ts/action/fields, increments counters
- event_count counts every record; action_count skips _EVENT_ONLY_ACTIONS
- _EVENT_ONLY_ACTIONS membership (console / download_saved / popup_opened)
- record() is single-line (no embedded newlines), ensure_ascii=False
- timestamp format ends with Z (not +00:00)
- close() idempotency (second call doesn't reraise on already-closed handle)
- multiple records produce one line each
- tail_log behaviours not already in tests/test_recording_tail.py
- new_log_path filename shape (label vs no label, kind in name, timestamp prefix)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from octowright.recorder import _EVENT_ONLY_ACTIONS, Recorder, new_log_path, tail_log

# ─── recording-file privacy (OCTOWRIGHT_RECORDINGS_PRIVATE) ─────────────────


class TestRecordingPrivacy:
    """The JSONL holds typed input, navigated URLs, console output — and, in
    legacy ``OCTOWRIGHT_REDACT_INPUTS=off`` deployments, cleartext credentials.
    A world-readable (0644) file lets any LOCAL user read all of that, bypassing
    the loopback HTTP boundary entirely. Default-on private mode forces 0600 on
    the file and 0700 on its parent. Opt out with the env var for shared-read
    setups."""

    def test_recording_file_is_0600_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_RECORDINGS_PRIVATE", raising=False)
        rec = Recorder(tmp_path / "sub" / "s.jsonl")
        rec.record("navigate", url="https://x")
        rec.close()
        assert (Path(rec.log_path).stat().st_mode & 0o777) == 0o600
        # parent dir locked to owner-only.
        assert (Path(rec.log_path).parent.stat().st_mode & 0o777) == 0o700

    def test_existing_world_readable_file_is_tightened(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # A recording left 0644 by an older daemon must be chmod-ed on reopen.
        monkeypatch.delenv("OCTOWRIGHT_RECORDINGS_PRIVATE", raising=False)
        p = tmp_path / "old.jsonl"
        p.write_text("")
        p.chmod(0o644)
        Recorder(p).close()
        assert (p.stat().st_mode & 0o777) == 0o600

    def test_opt_out_leaves_default_umask(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # off → Recorder does not force 0600 (operator opted into shared read).
        monkeypatch.setenv("OCTOWRIGHT_RECORDINGS_PRIVATE", "off")
        p = tmp_path / "shared.jsonl"
        p.write_text("")
        p.chmod(0o644)
        Recorder(p).close()
        assert (p.stat().st_mode & 0o777) == 0o644  # untouched


# ─── _EVENT_ONLY_ACTIONS membership ─────────────────────────────────────────


class TestEventOnlyActions:
    def test_membership_exact(self) -> None:
        """Mutating the set composition would change action_count semantics."""
        assert frozenset({"console", "download_saved", "popup_opened"}) == _EVENT_ONLY_ACTIONS

    def test_is_frozenset(self) -> None:
        """The set is frozen so the constant is immutable."""
        assert isinstance(_EVENT_ONLY_ACTIONS, frozenset)


# ─── Recorder.__init__ ─────────────────────────────────────────────────────


class TestRecorderInit:
    def test_creates_missing_parent_dirs(self, tmp_path: Path) -> None:
        """Parent directories created on construction (mkdir parents=True)."""
        target = tmp_path / "deep" / "nested" / "rec.jsonl"
        rec = Recorder(target)
        try:
            assert target.parent.exists()
            assert target.exists()
        finally:
            rec.close()

    def test_opens_in_append_mode(self, tmp_path: Path) -> None:
        """File is opened 'a' so existing content is preserved across re-opens."""
        target = tmp_path / "rec.jsonl"
        target.write_text('{"ts":"t","action":"prior"}\n', encoding="utf-8")
        rec = Recorder(target)
        try:
            rec.record("after")
        finally:
            rec.close()
        # Original line still there, new line appended.
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert "prior" in lines[0]
        assert "after" in lines[1]

    def test_initial_counters_zero(self, tmp_path: Path) -> None:
        """event_count and action_count both start at 0."""
        rec = Recorder(tmp_path / "rec.jsonl")
        try:
            assert rec.event_count == 0
            assert rec.action_count == 0
        finally:
            rec.close()

    def test_log_path_attribute(self, tmp_path: Path) -> None:
        """log_path attribute round-trips the constructor argument."""
        target = tmp_path / "rec.jsonl"
        rec = Recorder(target)
        try:
            assert rec.log_path == target
        finally:
            rec.close()


# ─── record() basics ────────────────────────────────────────────────────────


class TestRecordBasics:
    def test_writes_single_jsonl_line(self, tmp_path: Path) -> None:
        """record() appends one line with trailing newline."""
        target = tmp_path / "rec.jsonl"
        rec = Recorder(target)
        try:
            rec.record("click", selector="#x")
        finally:
            rec.close()
        text = target.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert text.count("\n") == 1

    def test_entry_has_ts_action_and_fields(self, tmp_path: Path) -> None:
        """JSON has ts, action, plus every kwarg in the entry."""
        target = tmp_path / "rec.jsonl"
        rec = Recorder(target)
        try:
            rec.record("click", selector="#x", value="abc")
        finally:
            rec.close()
        entry = json.loads(target.read_text(encoding="utf-8").strip())
        assert entry["action"] == "click"
        assert entry["selector"] == "#x"
        assert entry["value"] == "abc"
        assert "ts" in entry

    def test_timestamp_ends_with_z(self, tmp_path: Path) -> None:
        """Timestamp format ends with 'Z', not '+00:00' (mutating the replace would break)."""
        target = tmp_path / "rec.jsonl"
        rec = Recorder(target)
        try:
            rec.record("nav")
        finally:
            rec.close()
        entry = json.loads(target.read_text(encoding="utf-8").strip())
        assert entry["ts"].endswith("Z")
        assert "+" not in entry["ts"]

    def test_timestamp_iso_8601_shape(self, tmp_path: Path) -> None:
        """Timestamp is an ISO-8601 datetime string."""
        target = tmp_path / "rec.jsonl"
        rec = Recorder(target)
        try:
            rec.record("nav")
        finally:
            rec.close()
        entry = json.loads(target.read_text(encoding="utf-8").strip())
        # YYYY-MM-DDTHH:MM:SS.fffZ shape (microseconds optional).
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", entry["ts"])

    def test_multiple_records_produce_one_line_each(self, tmp_path: Path) -> None:
        """N record() calls → N lines, in order."""
        target = tmp_path / "rec.jsonl"
        rec = Recorder(target)
        try:
            rec.record("a")
            rec.record("b")
            rec.record("c")
        finally:
            rec.close()
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        actions = [json.loads(line)["action"] for line in lines]
        assert actions == ["a", "b", "c"]

    def test_no_embedded_newlines_in_line(self, tmp_path: Path) -> None:
        """A record with a multi-line string field still produces ONE jsonl line."""
        target = tmp_path / "rec.jsonl"
        rec = Recorder(target)
        try:
            rec.record("note", text="line1\nline2\nline3")
        finally:
            rec.close()
        # The stored line has the embedded newlines escaped as \n in JSON.
        raw = target.read_text(encoding="utf-8")
        # Exactly one trailing newline character for the line terminator.
        assert raw.endswith("\n")
        assert raw.count("\n") == 1
        entry = json.loads(raw.strip())
        assert entry["text"] == "line1\nline2\nline3"

    def test_ensure_ascii_false_allows_unicode(self, tmp_path: Path) -> None:
        """Unicode in a field is preserved (not \\uXXXX-escaped)."""
        target = tmp_path / "rec.jsonl"
        rec = Recorder(target)
        try:
            rec.record("note", text="héllo 🦑")
        finally:
            rec.close()
        # ensure_ascii=False → the actual UTF-8 bytes appear verbatim.
        raw = target.read_text(encoding="utf-8")
        assert "héllo 🦑" in raw


# ─── record() counter semantics ─────────────────────────────────────────────


class TestRecordCounters:
    def test_event_count_increments_for_every_record(self, tmp_path: Path) -> None:
        """event_count goes up on every call, including event-only actions."""
        rec = Recorder(tmp_path / "rec.jsonl")
        try:
            rec.record("click")
            rec.record("console", text="x")
            rec.record("popup_opened")
            assert rec.event_count == 3
        finally:
            rec.close()

    def test_action_count_skips_event_only_actions(self, tmp_path: Path) -> None:
        """action_count skips the _EVENT_ONLY_ACTIONS entries."""
        rec = Recorder(tmp_path / "rec.jsonl")
        try:
            rec.record("click")  # +1
            rec.record("console")  # event-only, skipped
            rec.record("download_saved")  # event-only, skipped
            rec.record("popup_opened")  # event-only, skipped
            rec.record("fill")  # +1
            assert rec.action_count == 2
            assert rec.event_count == 5
        finally:
            rec.close()

    def test_counters_are_read_only_properties(self, tmp_path: Path) -> None:
        """event_count and action_count are properties, not assignable directly."""
        rec = Recorder(tmp_path / "rec.jsonl")
        try:
            with pytest.raises(AttributeError):
                rec.event_count = 99  # type: ignore[misc]
        finally:
            rec.close()


# ─── close() idempotency ────────────────────────────────────────────────────


class TestRecorderClose:
    def test_close_marks_file_handle_closed(self, tmp_path: Path) -> None:
        """After close() the underlying file handle is closed."""
        rec = Recorder(tmp_path / "rec.jsonl")
        rec.close()
        assert rec._fh.closed is True

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        """Calling close() twice does not raise."""
        rec = Recorder(tmp_path / "rec.jsonl")
        rec.close()
        rec.close()  # must not raise

    def test_record_after_close_raises(self, tmp_path: Path) -> None:
        """Recording after close raises ValueError ("I/O operation on closed file")."""
        rec = Recorder(tmp_path / "rec.jsonl")
        rec.close()
        with pytest.raises(ValueError):
            rec.record("after_close")


# ─── tail_log behaviours not in test_recording_tail.py ─────────────────────


class TestTailLogExtras:
    def test_returns_total_bytes_for_existing_file(self, tmp_path: Path) -> None:
        """total_bytes reflects on-disk size."""
        target = tmp_path / "rec.jsonl"
        target.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
        events, cursor, total = tail_log(target, 0)
        assert total == target.stat().st_size
        assert cursor == total
        assert events == [{"a": 1}, {"b": 2}]

    def test_missing_file_returns_unchanged_cursor(self, tmp_path: Path) -> None:
        """Missing file → cursor echoed back, events empty, total=0."""
        events, cursor, total = tail_log(tmp_path / "does-not-exist.jsonl", 42)
        assert events == []
        assert cursor == 42
        assert total == 0

    def test_cursor_at_eof_returns_no_events(self, tmp_path: Path) -> None:
        """Cursor already at EOF → no new events, cursor unchanged, total reflects size."""
        target = tmp_path / "rec.jsonl"
        text = '{"a":1}\n{"b":2}\n'
        target.write_text(text, encoding="utf-8")
        size = target.stat().st_size
        events, cursor, total = tail_log(target, size)
        assert events == []
        assert cursor == size
        assert total == size

    def test_blank_line_skipped(self, tmp_path: Path) -> None:
        """A blank line in the middle of the JSONL stream is silently dropped."""
        target = tmp_path / "rec.jsonl"
        target.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")
        events, _, _ = tail_log(target, 0)
        assert events == [{"a": 1}, {"b": 2}]

    def test_no_newline_in_data_returns_empty_events(self, tmp_path: Path) -> None:
        """If the new bytes don't contain a complete line, events stays empty."""
        target = tmp_path / "rec.jsonl"
        target.write_text("{partial", encoding="utf-8")
        events, cursor, total = tail_log(target, 0)
        assert events == []
        # Cursor doesn't advance because there's no full line.
        assert cursor == 0
        assert total == target.stat().st_size

    def test_partial_trailing_line_cursor_stops_at_start_of_fragment(self, tmp_path: Path) -> None:
        """Trailing partial line's bytes are NOT consumed; next call re-reads them."""
        target = tmp_path / "rec.jsonl"
        # write_bytes avoids Windows' default '\n' → '\r\n' translation in text
        # mode; the cursor positions in the JSONL recorder are byte offsets.
        target.write_bytes(b'{"a":1}\n{"b":')
        events, cursor, _ = tail_log(target, 0)
        assert events == [{"a": 1}]
        # Cursor at exactly 8 (after `{"a":1}\n`).
        assert cursor == 8
        # Now finish the partial line and re-call.
        target.write_bytes(b'{"a":1}\n{"b":2}\n')
        events2, cursor2, _ = tail_log(target, cursor)
        assert events2 == [{"b": 2}]
        assert cursor2 == target.stat().st_size


# ─── new_log_path ───────────────────────────────────────────────────────────


class TestNewLogPath:
    def test_filename_includes_timestamp_kind_instance(self, tmp_path: Path) -> None:
        """Default filename: <stamp>-<kind>-<instance_id>.jsonl."""
        result = new_log_path(tmp_path, instance_id="abc123", label=None, kind="chromium")
        assert result.parent == tmp_path
        assert result.suffix == ".jsonl"
        # Stem matches the documented shape.
        assert re.match(r"^\d{8}T\d{6}Z-chromium-abc123$", result.stem)

    def test_filename_includes_label_when_provided(self, tmp_path: Path) -> None:
        """label suffix appears as -<label> before .jsonl."""
        result = new_log_path(tmp_path, instance_id="abc123", label="qa", kind="firefox")
        assert result.stem.endswith("-firefox-abc123-qa")

    def test_no_label_omits_suffix(self, tmp_path: Path) -> None:
        """label=None → no trailing -<label> token."""
        result = new_log_path(tmp_path, instance_id="abc123", label=None, kind="webkit")
        # Stem is exactly 3 dash-separated tokens after the timestamp.
        assert result.stem.split("-")[1] == "webkit"
        assert result.stem.split("-")[2] == "abc123"
        # No 4th dash-separated token.
        assert len(result.stem.split("-")) == 3

    def test_empty_label_omits_suffix(self, tmp_path: Path) -> None:
        """Empty-string label is falsy → no -<label> appended."""
        result = new_log_path(tmp_path, instance_id="abc123", label="", kind="chromium")
        # Same shape as label=None case.
        assert len(result.stem.split("-")) == 3

    def test_timestamp_is_utc_format(self, tmp_path: Path) -> None:
        """Timestamp prefix uses %Y%m%dT%H%M%SZ (compact, with trailing Z)."""
        result = new_log_path(tmp_path, instance_id="abc123", label=None, kind="chromium")
        stamp = result.stem.split("-")[0]
        assert stamp.endswith("Z")
        # Parses cleanly under the documented format.
        from datetime import datetime

        datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")

    def test_returns_path_object(self, tmp_path: Path) -> None:
        """Result is a Path, not a str."""
        result = new_log_path(tmp_path, instance_id="abc123", label=None, kind="chromium")
        assert isinstance(result, Path)
