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

from octowright.macros.redaction_text import (
    DIGIT_SEPARATORS,
    IGNORABLE_RANGES,
    JS_DIGIT_SEPARATOR_CLASS,
    JS_IGNORABLE_CLASS,
    MIN_DIGITS,
    digit_needles,
    digits_form,
    normalize,
)

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
            if codepoint < 0:
                continue
            character = chr(codepoint)
            if _ignorable(codepoint) or character.isspace() or unicodedata.normalize("NFKC", character) != character:
                continue
            assert normalize(f"a{character}") == f"a{character}".casefold()
            kept += 1
    assert kept >= len(IGNORABLE_RANGES) - 1


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


@pytest.mark.parametrize("control", ["\u0000", "\u0001", "\u001f", "\u007f", "\u0081", "\u009f"])
def test_control_characters_inside_a_value_are_removed(control: str) -> None:
    assert normalize(f"Probe-{control}Secret") == "probe-secret"


@pytest.mark.parametrize(
    ("value", "needles"),
    [
        ("+15550137788", ["15550137788", "5550137788", "550137788", "50137788", "0137788"]),
        ("+1 (555) 013-7788", ["15550137788", "5550137788", "550137788", "50137788", "0137788"]),
        ("555.013/7788", ["5550137788", "550137788", "50137788", "0137788"]),
        ("\uff15\uff15\uff15\uff10\uff11\uff13\uff17", ["5550137"]),
        ("1234567", ["1234567"]),
    ],
    ids=["plus-digits", "formatted", "dots-and-slash", "fullwidth", "exactly-min"],
)
def test_a_numeric_value_is_found_by_its_digits_and_each_long_enough_ending(value: str, needles: list[str]) -> None:
    assert digit_needles(value) == needles
    assert all(len(needle) >= MIN_DIGITS for needle in needles)


@pytest.mark.parametrize(
    "value", ["123456", "call 5550137788", "5550137788x", "\u0665\u0665\u0665\u0660\u0661\u0663\u0667", ""]
)
def test_a_short_or_not_purely_numeric_value_has_no_digit_spelling(value: str) -> None:
    assert digit_needles(value) == []


def test_digits_form_drops_number_punctuation_whitespace_and_invisible_characters_only() -> None:
    assert digits_form("+1 (555)\u200b013-7788.") == "15550137788"
    assert digits_form("Ab+1") == "Ab1"
    assert set(DIGIT_SEPARATORS) == set("+-.()/")


def test_the_javascript_separator_class_lists_exactly_the_separators() -> None:
    parsed = "".join(chr(int(code, 16)) for code in re.findall(r"\\u\{([0-9A-F]+)\}", JS_DIGIT_SEPARATOR_CLASS))
    assert parsed == DIGIT_SEPARATORS
    assert re.sub(r"\\u\{[0-9A-F]+\}", "", JS_DIGIT_SEPARATOR_CLASS) == ""
