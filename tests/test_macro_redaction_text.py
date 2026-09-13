# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The one definition of "the same value" that the page and the snapshot scan share."""

from __future__ import annotations

import itertools
import re
import unicodedata

import pytest

from octowright.macros.redaction_text import IGNORABLE_RANGES, JS_IGNORABLE_CLASS, normalize

_BOUNDARIES = sorted({codepoint for low, high in IGNORABLE_RANGES for codepoint in (low, high)})


def _ignorable(codepoint: int) -> bool:
    return any(low <= codepoint <= high for low, high in IGNORABLE_RANGES)


@pytest.mark.parametrize("codepoint", _BOUNDARIES, ids=[f"U+{codepoint:04X}" for codepoint in _BOUNDARIES])
def test_every_ignorable_range_boundary_is_removed(codepoint: int) -> None:
    assert normalize(f"ab{chr(codepoint)}cd") == "abcd"


def test_characters_just_outside_each_range_are_kept() -> None:
    kept = 0
    for low, high in IGNORABLE_RANGES:
        for codepoint in (low - 1, high + 1):
            character = chr(codepoint)
            if _ignorable(codepoint) or character.isspace() or unicodedata.normalize("NFKC", character) != character:
                continue
            assert normalize(f"a{character}") == f"a{character}".casefold()
            kept += 1
    assert kept >= len(IGNORABLE_RANGES)


def test_the_ranges_are_ordered_and_disjoint() -> None:
    assert all(low <= high for low, high in IGNORABLE_RANGES)
    assert all(before[1] < after[0] for before, after in itertools.pairwise(IGNORABLE_RANGES))


def test_case_width_composition_and_whitespace_are_ignored() -> None:
    assert normalize("Jos\u00e9") == normalize("Jose\u0301") == "jos\u00e9"
    assert normalize("\uff30\uff32\uff2f\uff22\uff25") == "probe"
    assert normalize(" a\tb\nc\u00a0d ") == "abcd"


def test_the_javascript_class_lists_exactly_the_same_ranges() -> None:
    pair = r"\\u\{([0-9A-F]+)\}-\\u\{([0-9A-F]+)\}"
    parsed = tuple((int(low, 16), int(high, 16)) for low, high in re.findall(pair, JS_IGNORABLE_CLASS))
    assert parsed == IGNORABLE_RANGES
    assert re.sub(pair, "", JS_IGNORABLE_CLASS) == ""
