# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Character -> physical key mapping, for typing that has to hold Shift for real.

Playwright's ``page.type()`` dispatches ``keydown``/``keypress`` events carrying
the right ``key``/``text`` payload but never holds the Shift modifier down. A
DOM ``<input>`` reads that payload and is unaffected, which is why this is
invisible on ordinary forms. A canvas-based app -- a KVM console (AMI
H5Viewer), a canvas terminal, anything rendering its own text instead of using
real DOM inputs -- reads ``code`` + ``shiftKey`` and converts that to HID
scancodes. It never sees the payload, so Shift is silently dropped and every
shifted character lands as its unshifted twin: ``echo TYPE=Ab*:`` arrives as
``echo type=ab8;``. Verified against a real H5Viewer on 2026-08-19.

This module answers the one question the fix needs: for character *c*, which
physical key is it on and is Shift held? ``session/core_page_mixin.type_text``
uses that to press keys the way a person does, so ``shiftKey`` is genuinely set.

**This table is US QWERTY**, and that is a real limitation rather than an
oversight. A character's physical key is a property of the *layout*, not of
the character -- ``*`` is Shift+Digit8 here and somewhere else entirely on
AZERTY -- and nothing on the wire tells us which layout the target believes it
has. Playwright's own ``code`` generation assumes US QWERTY for the same
reason. So the mapping is correct for the case it was built for and wrong for
a target configured otherwise, which is why keystroke mode is opt-in
(``key_mode="keys"``) rather than the default.

Pure data plus one lookup: no Playwright import, so the table can be tested
without a browser.
"""

from __future__ import annotations

from typing import Final

# Unshifted -> shifted, for every US-QWERTY key that carries two characters.
# The key name on the left of each pair is Playwright's ``code``.
_PUNCTUATION: Final[dict[str, tuple[str, str]]] = {
    # code: (unshifted char, shifted char)
    "Backquote": ("`", "~"),
    "Digit1": ("1", "!"),
    "Digit2": ("2", "@"),
    "Digit3": ("3", "#"),
    "Digit4": ("4", "$"),
    "Digit5": ("5", "%"),
    "Digit6": ("6", "^"),
    "Digit7": ("7", "&"),
    "Digit8": ("8", "*"),
    "Digit9": ("9", "("),
    "Digit0": ("0", ")"),
    "Minus": ("-", "_"),
    "Equal": ("=", "+"),
    "BracketLeft": ("[", "{"),
    "BracketRight": ("]", "}"),
    "Backslash": ("\\", "|"),
    "Semicolon": (";", ":"),
    "Quote": ("'", '"'),
    "Comma": (",", "<"),
    "Period": (".", ">"),
    "Slash": ("/", "?"),
}

# Whitespace that has a key of its own. A newline is Enter rather than a
# character; without it a multi-line send would drop every line break.
_WHITESPACE: Final[dict[str, tuple[str, bool]]] = {
    " ": ("Space", False),
    "\t": ("Tab", False),
    "\n": ("Enter", False),
    "\r": ("Enter", False),
}


def _build() -> dict[str, tuple[str, bool]]:
    table: dict[str, tuple[str, bool]] = {}
    for code, (plain, shifted) in _PUNCTUATION.items():
        table[plain] = (code, False)
        table[shifted] = (code, True)
    for letter_ord in range(ord("a"), ord("z") + 1):
        lower = chr(letter_ord)
        code = f"Key{lower.upper()}"
        table[lower] = (code, False)
        table[lower.upper()] = (code, True)
    table.update(_WHITESPACE)
    return table


_TABLE: Final[dict[str, tuple[str, bool]]] = _build()


def keystroke_for(char: str) -> tuple[str, bool] | None:
    """The ``(code, shift_held)`` that produces *char* on US QWERTY.

    Returns ``None`` for anything the layout has no physical key for -- an
    accented letter, an emoji, any non-ASCII character. The caller is expected
    to fall back to Playwright's own text insertion for those rather than
    guessing a key; a character with no scancode cannot be delivered as one.
    """
    return _TABLE.get(char)
