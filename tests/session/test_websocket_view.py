# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Reading frames back out of the websocket sidecar."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.session.websocket_view import (
    WEBSOCKET_MESSAGES_DEFAULT_LIMIT,
    WEBSOCKET_MESSAGES_MAX_LIMIT,
    read_frames,
    resolve_message_limit,
    summarize_sockets,
)


def _sidecar(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "s.websocket.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _frame(direction: str, text: str, socket_id: str = "s1") -> dict:
    return {
        "ts": "2026-09-05T00:00:00.000Z",
        "action": f"websocket_frame{direction}",
        "id": socket_id,
        "url": "ws://app.test/stream",
        "payload_preview": text,
        "payload_text": text,
        "payload_size": len(text),
    }


class TestLimitResolution:
    def test_default_when_unspecified(self) -> None:
        assert resolve_message_limit(None) == WEBSOCKET_MESSAGES_DEFAULT_LIMIT

    @pytest.mark.parametrize("value", [0, -1, -1000])
    def test_non_positive_falls_back_to_default_not_unlimited(self, value: int) -> None:
        """An LLM must not be able to remove the cap by passing zero."""
        assert resolve_message_limit(value) == WEBSOCKET_MESSAGES_DEFAULT_LIMIT

    def test_explicit_value_honoured(self) -> None:
        assert resolve_message_limit(25) == 25

    def test_ceiling_applies(self) -> None:
        assert resolve_message_limit(999_999) == WEBSOCKET_MESSAGES_MAX_LIMIT


class TestReadFrames:
    def test_directions_are_named_by_who_sent_them(self, tmp_path: Path) -> None:
        """'framesent'/'framereceived' are Playwright event names; a caller
        should read 'sent'/'received'."""
        path = _sidecar(tmp_path, [_frame("sent", "a"), _frame("received", "b")])
        result = read_frames(path)
        assert [m["direction"] for m in result["messages"]] == ["sent", "received"]

    def test_payloads_are_previews_unless_asked_for(self, tmp_path: Path) -> None:
        """A busy socket emits thousands of frames; full bodies are opt-in."""
        path = _sidecar(tmp_path, [_frame("sent", "hello")])
        message = read_frames(path)["messages"][0]
        assert message["preview"] == "hello"
        assert "payload_text" not in message

    def test_include_payloads_returns_the_body(self, tmp_path: Path) -> None:
        path = _sidecar(tmp_path, [_frame("sent", "hello")])
        message = read_frames(path, include_payloads=True)["messages"][0]
        assert message["payload_text"] == "hello"

    def test_binary_is_flagged_and_kept_separate_from_text(self, tmp_path: Path) -> None:
        """A caller decoding base64 must not have to guess whether it is
        looking at base64 or text that happens to resemble it."""
        row = _frame("received", "")
        del row["payload_text"]
        row["payload_b64"] = "AAEC"
        path = _sidecar(tmp_path, [row])
        message = read_frames(path, include_payloads=True)["messages"][0]
        assert message["binary"] is True
        assert message["payload_b64"] == "AAEC"
        assert "payload_text" not in message

    def test_filters_by_socket_and_direction(self, tmp_path: Path) -> None:
        path = _sidecar(
            tmp_path,
            [_frame("sent", "a", "s1"), _frame("received", "b", "s1"), _frame("sent", "c", "s2")],
        )
        assert [m["preview"] for m in read_frames(path, socket_id="s2")["messages"]] == ["c"]
        assert [m["preview"] for m in read_frames(path, direction="sent")["messages"]] == ["a", "c"]

    def test_non_frame_rows_are_skipped(self, tmp_path: Path) -> None:
        """The sidecar also carries the recorder's truncation marker."""
        path = _sidecar(
            tmp_path,
            [{"action": "websocket_truncated", "limit_bytes": 1, "bytes_written": 2}, _frame("sent", "a")],
        )
        assert [m["preview"] for m in read_frames(path)["messages"]] == ["a"]

    def test_limit_caps_and_flags_truncation(self, tmp_path: Path) -> None:
        path = _sidecar(tmp_path, [_frame("sent", str(i)) for i in range(10)])
        result = read_frames(path, limit=3)
        assert result["returned"] == 3
        assert result["truncated"] is True

    def test_a_full_page_is_not_flagged_truncated(self, tmp_path: Path) -> None:
        path = _sidecar(tmp_path, [_frame("sent", str(i)) for i in range(3)])
        result = read_frames(path, limit=3)
        assert (result["returned"], result["truncated"]) == (3, False)

    def test_missing_sidecar_returns_empty_not_an_error(self, tmp_path: Path) -> None:
        """A session whose page never opened a socket is the common case."""
        result = read_frames(None)
        assert result == {"messages": [], "next_cursor": 0, "returned": 0, "truncated": False, "total_bytes": 0}


class TestSummarize:
    def test_open_and_closed_are_partitioned(self, tmp_path: Path) -> None:
        """The usual question is which sockets are live right now, so burying
        them among finished ones answers it badly."""
        registry = {
            "a": {"id": "a", "closed_at": None},
            "b": {"id": "b", "closed_at": "2026-09-05T00:00:01Z"},
        }
        summary = summarize_sockets(registry, dropped=0)
        assert summary["open_count"] == 1
        assert summary["closed_count"] == 1
        assert summary["open"][0]["id"] == "a"

    def test_dropped_is_reported_so_a_shrinking_count_is_explainable(self) -> None:
        assert summarize_sockets({}, dropped=7)["dropped"] == 7
