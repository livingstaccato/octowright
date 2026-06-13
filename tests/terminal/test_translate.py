# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.terminal.translate import MessageTranslator


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


def test_error_message_maps_to_terminal_error() -> None:
    t = MessageTranslator()
    assert t.feed({"type": "error", "message": "boom"}) == [("terminal_error", {"message": "boom"})]


def test_unknown_type_passes_through_as_terminal_event() -> None:
    t = MessageTranslator()
    out = t.feed({"type": "worker_hello", "input_mode": "open"})
    assert out == [("terminal_event", {"uterm_type": "worker_hello", "input_mode": "open"})]
