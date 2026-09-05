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
    WEBSOCKET_MESSAGES_MAX_RESPONSE_CHARS,
    WEBSOCKET_PAYLOAD_MAX_CHARS,
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
        assert result == {
            "messages": [],
            "next_cursor": 0,
            "returned": 0,
            "truncated": False,
            "total_bytes": 0,
            "capture_truncated": False,
            "capture_limit_bytes": None,
        }


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


class TestPaging:
    """The cursor has to name the first frame we did NOT return.

    Shipped naming the end of the whole read window instead, so a capped read
    handed back a cursor past every frame it had skipped: paging with it lost
    frames 3-9 of ten, and at the real defaults (cap 100, 8 MiB window) lost
    4,900 of five thousand. ``core_network_mixin._page_requests`` already
    states the rule this restores.
    """

    @pytest.mark.parametrize(
        ("tail_max_bytes", "limit"),
        [
            # The row cap ends each page ...
            (None, 3),
            # ... and the read window ends each page, which is the second way
            # a page can be short and the one `truncated` used to miss.
            ("300", 100),
        ],
    )
    def test_paging_reaches_every_frame(self, tmp_path: Path, monkeypatch, tail_max_bytes, limit) -> None:  # type: ignore[no-untyped-def]
        if tail_max_bytes is not None:
            monkeypatch.setenv("OCTOWRIGHT_TAIL_MAX_BYTES", tail_max_bytes)
        path = _sidecar(tmp_path, [_frame("sent", str(i)) for i in range(10)])
        seen: list[str] = []
        cursor = 0
        for _ in range(40):
            result = read_frames(path, cursor=cursor, limit=limit)
            seen.extend(m["preview"] for m in result["messages"])
            cursor = result["next_cursor"]
            if not result["truncated"]:
                break
        assert seen == [str(i) for i in range(10)]

    def test_the_cursor_points_at_the_first_unreturned_frame(self, tmp_path: Path) -> None:
        path = _sidecar(tmp_path, [_frame("sent", str(i)) for i in range(10)])
        first = read_frames(path, cursor=0, limit=3)
        assert first["next_cursor"] < first["total_bytes"]
        assert read_frames(path, cursor=first["next_cursor"], limit=1)["messages"][0]["preview"] == "3"

    def test_a_cut_read_window_is_reported_as_truncated(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """``truncated`` said only "the row cap bit".

        A caller told to page while it is true stops early holding a prefix
        whenever the byte window cut the file first. ``browser_tail_recording``
        derives the same thing correctly as ``new_cursor >= total_bytes``.
        """
        monkeypatch.setenv("OCTOWRIGHT_TAIL_MAX_BYTES", "300")
        path = _sidecar(tmp_path, [_frame("sent", str(i)) for i in range(10)])
        result = read_frames(path, limit=100)
        assert result["returned"] < 10
        assert result["truncated"] is True

    def test_a_negative_cursor_is_clamped_not_an_oserror(self, tmp_path: Path) -> None:
        """``cursor`` is an LLM-supplied int; ``fh.seek`` raises Errno 22 on it."""
        path = _sidecar(tmp_path, [_frame("sent", "a")])
        assert read_frames(path, cursor=-5)["returned"] == 1


class TestCaptureTruncationIsVisible:
    """The marker exists so inspection can see the cut, and inspection dropped it."""

    def test_the_marker_is_surfaced_rather_than_only_skipped(self, tmp_path: Path) -> None:
        path = _sidecar(
            tmp_path,
            [_frame("sent", "a"), {"action": "websocket_truncated", "limit_bytes": 64, "bytes_written": 65}],
        )
        result = read_frames(path)
        assert [m["preview"] for m in result["messages"]] == ["a"]
        assert result["capture_truncated"] is True
        assert result["capture_limit_bytes"] == 64

    def test_an_uncut_capture_says_so(self, tmp_path: Path) -> None:
        path = _sidecar(tmp_path, [_frame("sent", "a")])
        assert read_frames(path)["capture_truncated"] is False


class TestResponseSize:
    """A row budget does not bound bytes -- the lesson MACRO_FAILURE_CONSOLE_TEXT_CHARS records."""

    def test_one_huge_payload_is_capped_and_flagged(self, tmp_path: Path) -> None:
        path = _sidecar(tmp_path, [_frame("received", "x" * (WEBSOCKET_PAYLOAD_MAX_CHARS + 500))])
        message = read_frames(path, include_payloads=True)["messages"][0]
        assert len(message["payload_text"]) == WEBSOCKET_PAYLOAD_MAX_CHARS
        assert message["payload_truncated"] is True

    def test_many_payloads_stop_at_the_response_budget(self, tmp_path: Path) -> None:
        chunk = "y" * 10_000
        path = _sidecar(tmp_path, [_frame("received", chunk, str(i)) for i in range(200)])
        result = read_frames(path, include_payloads=True, limit=200)
        assert result["returned"] < 200
        assert result["truncated"] is True
        body = sum(len(m.get("payload_text", "")) for m in result["messages"])
        assert body <= WEBSOCKET_MESSAGES_MAX_RESPONSE_CHARS

    def test_the_budget_still_returns_at_least_one_frame(self, tmp_path: Path) -> None:
        """A frame bigger than the whole budget must not page forever."""
        path = _sidecar(tmp_path, [_frame("received", "z" * 200_000) for _ in range(3)])
        result = read_frames(path, include_payloads=True, limit=200)
        assert result["returned"] >= 1


class TestSummaryIsolation:
    def test_entries_are_copies_of_the_live_registry(self) -> None:
        """``list(registry.values())`` copies the list, not the dicts in it.

        The frame handler keeps mutating those same dicts, so a caller that
        stashed a summary watched it change underneath -- the defect already
        fixed once in ``_select_console_tail``.
        """
        registry = {"a": {"id": "a", "closed_at": None, "framesent": 1}}
        summary = summarize_sockets(registry, dropped=0)
        registry["a"]["framesent"] = 99
        assert summary["open"][0]["framesent"] == 1


class TestSocketIdType:
    def test_socket_id_matches_the_summary_id_type(self, tmp_path: Path) -> None:
        """The summary stringifies its ``id``; frames returned the raw value,
        so a caller joining the two by dict key or ``==`` matched nothing."""
        row = _frame("sent", "a")
        row["id"] = 140_234_567
        path = _sidecar(tmp_path, [row])
        assert read_frames(path)["messages"][0]["socket_id"] == "140234567"


class TestCaptureTruncationIsSessionWide:
    """The marker is written ONCE, at the end of the sidecar.

    Detecting it only when a page's own window happens to contain it made the
    field page-local: a caller paging past it, or polling from the cursor it
    returned, was told `capture_truncated: false` and would conclude the
    capture was complete. The session knows the answer for its whole life, so
    it seeds the read.
    """

    def test_a_seeded_truncation_is_reported_without_the_marker(self, tmp_path: Path) -> None:
        path = _sidecar(tmp_path, [_frame("sent", "a")])
        assert read_frames(path, capture_truncated=True)["capture_truncated"] is True

    def test_the_marker_still_reports_it_when_unseeded(self, tmp_path: Path) -> None:
        path = _sidecar(
            tmp_path,
            [_frame("sent", "a"), {"action": "websocket_truncated", "limit_bytes": 64, "bytes_written": 65}],
        )
        assert read_frames(path)["capture_truncated"] is True


class TestResponseBudgetCoversEveryReturnedString:
    """A frame costs more than its payload.

    The budget counted preview and payloads only, so a page that opens a
    socket with a very long URL -- which the PAGE chooses, and which is
    repeated on every row -- could return a thousand rows of megabytes while
    the counter stayed under the limit, recreating the oversized response the
    budget exists to prevent.
    """

    def test_a_long_url_with_tiny_payloads_still_ends_the_page(self, tmp_path: Path) -> None:
        rows = []
        for i in range(200):
            row = _frame("received", "x", str(i))
            row["url"] = "ws://app.test/" + "u" * 20_000
            rows.append(row)
        result = read_frames(_sidecar(tmp_path, rows), limit=200)
        assert result["returned"] < 200
        assert result["truncated"] is True
