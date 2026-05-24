# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit tests for ``octowright._wire_utils.looks_like_binary_text``.

The helper used to be duplicated between ``session/core_io_mixin.py`` and
``http/discovery.py``. Both call sites now import this one function; the
tests below pin the heuristic so a refactor of either consumer can't
silently change the binary-detection contract.
"""

from __future__ import annotations

from octowright._wire_utils import looks_like_binary_text


def test_plain_ascii_string_is_not_binary() -> None:
    """Regular text payloads must NOT be flagged."""
    assert looks_like_binary_text("hello world") is False
    assert looks_like_binary_text("GET /index.html HTTP/1.1") is False


def test_utf8_emoji_string_is_not_binary() -> None:
    """Multi-byte UTF-8 content (emoji + accents) is still text, not binary."""
    assert looks_like_binary_text("café 💩 Привет") is False


def test_empty_payload_is_not_binary() -> None:
    """By convention an empty payload returns ``False`` — no enclosing
    quotes to match. The recorder layer treats empty bytes/strings
    separately before the heuristic ever runs."""
    assert looks_like_binary_text("") is False


def test_repr_of_png_bytes_is_binary() -> None:
    """A string that came from ``repr(png_bytes)`` matches the heuristic."""
    png_signature = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    assert looks_like_binary_text(repr(png_signature)) is True


def test_str_of_png_bytes_is_binary() -> None:
    """``str(bytes_obj)`` produces the same ``b'...'`` shape as ``repr``."""
    png = b"\x89PNG\x00\x00"
    assert looks_like_binary_text(str(png)) is True


def test_double_quoted_bytes_repr_is_binary() -> None:
    """Python sometimes uses ``b"..."`` (double-quote) when content has
    single quotes inside; we must catch both spellings."""
    assert looks_like_binary_text('b"hello\'world"') is True


def test_string_starting_with_b_but_not_quoted_is_not_binary() -> None:
    """Sentence beginning with the letter 'b' must NOT be misflagged."""
    assert looks_like_binary_text("breakfast was great") is False
    assert looks_like_binary_text("b is a letter") is False


def test_string_with_b_prefix_but_no_closing_quote_is_not_binary() -> None:
    """Malformed pseudo-repr without matching close quote → False."""
    assert looks_like_binary_text("b'truncated") is False
    assert looks_like_binary_text('b"truncated') is False


def test_non_string_payloads_return_false() -> None:
    """The check accepts ``Any`` for caller convenience; non-strings → False."""
    assert looks_like_binary_text(None) is False
    assert looks_like_binary_text(b"raw bytes") is False
    assert looks_like_binary_text(123) is False
    assert looks_like_binary_text(["b'list'"]) is False
    assert looks_like_binary_text({"b": "dict"}) is False


def test_minimal_quoted_bytes_repr_is_binary() -> None:
    """Smallest valid match: ``b''`` and ``b\"\"`` (empty bytes repr)."""
    assert looks_like_binary_text("b''") is True
    assert looks_like_binary_text('b""') is True
