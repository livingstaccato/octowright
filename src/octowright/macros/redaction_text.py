# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What counts as the same classified value, shared by the page and the snapshot scan.

The in-page redaction (:mod:`octowright.macros.redaction_page_js`) and the rendered-surface
scan (:mod:`octowright.macros.rendered_surface`) must agree on it. A spelling one side
treats as different is a spelling that reaches the screenshot.

Two spellings are the same when they are equal after NFKC normalization, after removing
whitespace, control characters and every Unicode default-ignorable code point, and after
ignoring case. Such a character renders as nothing (zero-width space, soft hyphen, joiners,
bidi controls, variation selectors, C0 and C1 controls), so a page can put one inside a
value without changing what a reader sees.

A value made only of digits and the punctuation numbers are displayed with (a phone
number, say) is also the same as any spelling of its digits with other such punctuation,
and a displayed number that ends the value in at least ``MIN_DIGITS`` digits holds it: a
page that formats ``+15550137788`` as ``(555) 013-7788`` still shows it.
"""

from __future__ import annotations

import re
import unicodedata

#: Characters that render as nothing: C0 and C1 controls, and Unicode
#: ``Default_Ignorable_Code_Point`` ranges from ``DerivedCoreProperties.txt``.
IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x0000, 0x001F),
    (0x007F, 0x009F),
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)

#: The same ranges as the body of a JavaScript ``u``-flag character class.
JS_IGNORABLE_CLASS = "".join(f"\\u{{{low:X}}}-\\u{{{high:X}}}" for low, high in IGNORABLE_RANGES)

#: Punctuation a displayed number is written with, besides whitespace.
DIGIT_SEPARATORS = "+-.()/"

#: The same characters as the body of a JavaScript character class.
JS_DIGIT_SEPARATOR_CLASS = "".join(f"\\u{{{ord(character):X}}}" for character in DIGIT_SEPARATORS)

#: The fewest trailing digits of a numeric value that still identify it on a page.
MIN_DIGITS = 7

_IGNORABLE_CLASS = "".join(f"\\U{low:08X}-\\U{high:08X}" for low, high in IGNORABLE_RANGES)
_INVISIBLE = re.compile("[\\s" + _IGNORABLE_CLASS + "]+")
_INVISIBLE_OR_SEPARATOR = re.compile("[\\s" + _IGNORABLE_CLASS + re.escape(DIGIT_SEPARATORS) + "]+")
_ASCII_DIGITS = re.compile("[0-9]+")


def normalize(text: str) -> str:
    """The comparable form of ``text``: NFKC, no whitespace or ignorable characters, casefolded."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text)).casefold()


def digits_form(text: str) -> str:
    """``text`` after NFKC with whitespace, ignorable characters and number punctuation removed."""
    return _INVISIBLE_OR_SEPARATOR.sub("", unicodedata.normalize("NFKC", text))


def digit_needles(value: str) -> list[str]:
    """The digit spellings a numeric ``value`` is found by: its digits, and each shorter ending.

    Empty unless ``value`` is only digits, whitespace, ignorable characters and
    ``DIGIT_SEPARATORS``, with at least ``MIN_DIGITS`` digits. Longest first.
    """
    digits = digits_form(value)
    if len(digits) < MIN_DIGITS or not _ASCII_DIGITS.fullmatch(digits):
        return []
    return [digits[start:] for start in range(len(digits) - MIN_DIGITS + 1)]
