# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The US-QWERTY character -> physical key table (``session/keyboard_layout``).

The cases that matter are the shifted ones: an unshifted character was never
broken, and a table that got those right while dropping Shift would reproduce
the exact bug this exists to fix.
"""

from __future__ import annotations

import pytest

from octowright.session.keyboard_layout import keystroke_for


@pytest.mark.parametrize(
    ("char", "code", "shift"),
    [
        # Every character from the field report, with what it WAS arriving as
        # when Shift was dropped: T->t, A->a, *->8, :->;
        ("T", "KeyT", True),
        ("A", "KeyA", True),
        ("*", "Digit8", True),
        (":", "Semicolon", True),
        # The unshifted twins each of those was collapsing to, proving the
        # table distinguishes them by the Shift flag and not by the key.
        ("t", "KeyT", False),
        ("a", "KeyA", False),
        ("8", "Digit8", False),
        (";", "Semicolon", False),
        # The rest of the shell metacharacters the report calls out as unsafe.
        ('"', "Quote", True),
        ("|", "Backslash", True),
        (">", "Period", True),
        ("~", "Backquote", True),
        # Whitespace has a key rather than a character.
        (" ", "Space", False),
        ("\n", "Enter", False),
        ("\t", "Tab", False),
    ],
)
def test_keystroke_for_maps_char_to_code_and_shift(char: str, code: str, shift: bool) -> None:
    assert keystroke_for(char) == (code, shift)


def test_every_shifted_char_shares_its_key_with_its_unshifted_twin() -> None:
    """A dropped Shift lands on the twin, so the pairing IS the bug's mechanism."""
    for plain, shifted in [("1", "!"), ("8", "*"), (";", ":"), ("'", '"'), (",", "<"), ("/", "?")]:
        plain_code, plain_shift = keystroke_for(plain)  # type: ignore[misc]
        shifted_code, shifted_shift = keystroke_for(shifted)  # type: ignore[misc]
        assert plain_code == shifted_code
        assert (plain_shift, shifted_shift) == (False, True)


def test_unmappable_characters_return_none() -> None:
    """No physical key means no scancode; the caller must fall back, not guess."""
    for char in ("é", "→", "🐙", "ß"):
        assert keystroke_for(char) is None


def test_full_printable_ascii_is_covered() -> None:
    """Anything a caller can reasonably type must resolve, or it silently
    falls back to the very payload path that drops Shift."""
    unmapped = [chr(c) for c in range(0x20, 0x7F) if keystroke_for(chr(c)) is None]
    assert unmapped == []
