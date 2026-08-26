# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright_terminal.translate import MessageTranslator


def _snap(screen: str) -> dict:
    return {"type": "snapshot", "screen": screen, "cursor": {"row": 0, "col": 0}, "screen_hash": "h"}


def test_first_snapshot_emits_full_screen_as_output() -> None:
    t = MessageTranslator()
    out = t.feed(_snap("hello"))
    assert out == [("terminal_output", {"data": "hello", "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})]


def test_clean_prefix_extension_emits_only_delta() -> None:
    t = MessageTranslator()
    t.feed(_snap("hello"))
    out = t.feed(_snap("hello world"))
    assert out == [("terminal_output", {"data": " world", "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})]


def test_identical_screen_emits_nothing() -> None:
    t = MessageTranslator()
    t.feed(_snap("hello"))
    assert t.feed(_snap("hello")) == []


def test_buffer_rotation_uses_suffix_overlap() -> None:
    # When the 32KB cap slides, the new buffer is not a prefix-extension; the
    # delta is the part of the new buffer past its overlap with the old one.
    t = MessageTranslator()
    t.feed(_snap("ABCDE"))
    out = t.feed(_snap("CDEFG"))  # overlap "CDE", new tail "FG"
    assert out == [("terminal_output", {"data": "FG", "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})]


def test_clear_to_empty_emits_reset() -> None:
    # connector.clear() sets buffer="" -> shorter, no overlap. The screen must be
    # cleared, so emit a reset even though the new delta is empty.
    t = MessageTranslator()
    t.feed(_snap("lots of old output"))
    out = t.feed(_snap(""))
    assert out == [("terminal_output", {"data": "", "reset": True, "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})]


def test_clear_then_fresh_content_emits_reset_with_full_buffer() -> None:
    # After clear() the buffer rebuilds from empty with unrelated, shorter
    # content -> reset + the full new buffer (not appended below the stale one).
    t = MessageTranslator()
    t.feed(_snap("aaaaaaaaaa\nbbbbbbbbbb\nbash-3.2$ "))
    out = t.feed(_snap("bash-3.2$ ls\r\nfile\r\n"))
    assert out == [
        (
            "terminal_output",
            {"data": "bash-3.2$ ls\r\nfile\r\n", "reset": True, "cursor": {"row": 0, "col": 0}, "screen_hash": "h"},
        )
    ]


def test_cap_slide_with_repeated_lead_char_finds_correct_overlap() -> None:
    # cur[0] occurs in prev before the real overlap start; the scan must skip
    # the false occurrence and find the true one (a grown, front-dropped buffer).
    t = MessageTranslator()
    t.feed(_snap("CXCDE"))
    out = t.feed(_snap("CDEFGH"))  # drop "CX", keep "CDE", append "FGH"
    assert out == [("terminal_output", {"data": "FGH", "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})]


def test_grew_but_no_overlap_emits_reset() -> None:
    # Buffer is at/above its prior length yet shares no overlap (e.g. clear()
    # followed by a large fresh dump) -> reset + full buffer, never appended.
    t = MessageTranslator()
    t.feed(_snap("AAAA"))
    out = t.feed(_snap("BBBBB"))
    assert out == [
        ("terminal_output", {"data": "BBBBB", "reset": True, "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})
    ]


def test_reset_not_fooled_by_coincidental_prompt_overlap() -> None:
    # The fresh post-clear buffer happens to START with the same prompt the old
    # buffer ENDED with. A longest-overlap search alone would treat that as a
    # cap-slide append; the length test correctly classifies it as a reset.
    t = MessageTranslator()
    t.feed(_snap("old line 1\r\nold line 2\r\nbash-3.2$ "))
    out = t.feed(_snap("bash-3.2$ "))  # shorter; shares the "bash-3.2$ " prompt
    assert out == [
        ("terminal_output", {"data": "bash-3.2$ ", "reset": True, "cursor": {"row": 0, "col": 0}, "screen_hash": "h"})
    ]


def test_error_message_maps_to_terminal_error() -> None:
    t = MessageTranslator()
    assert t.feed({"type": "error", "message": "boom"}) == [("terminal_error", {"message": "boom"})]


def test_unknown_type_passes_through_as_terminal_event() -> None:
    t = MessageTranslator()
    out = t.feed({"type": "worker_hello", "input_mode": "open"})
    assert out == [("terminal_event", {"uterm_type": "worker_hello", "input_mode": "open"})]
