# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What counts as the same classified value, shared by the page and the snapshot scan.

The in-page redaction (:mod:`octowright.macros.redaction_page_js`) and the rendered-surface
scan (:mod:`octowright.macros.rendered_surface`) must agree on it. A spelling one side
treats as different is a spelling that reaches the screenshot.

Two spellings are the same when they are equal after NFKC normalization, after removing
whitespace and every Unicode default-ignorable code point, and after ignoring case. A
default-ignorable character renders as nothing (zero-width space, soft hyphen, joiners,
bidi controls, variation selectors), so a page can put one inside a value without
changing what a reader sees.
"""

from __future__ import annotations

import re
import unicodedata

#: Unicode ``Default_Ignorable_Code_Point`` ranges, from ``DerivedCoreProperties.txt``.
IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
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

_INVISIBLE = re.compile("[\\s" + "".join(f"\\U{low:08X}-\\U{high:08X}" for low, high in IGNORABLE_RANGES) + "]+")


def normalize(text: str) -> str:
    """The comparable form of ``text``: NFKC, no whitespace or ignorable characters, casefolded."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text)).casefold()
