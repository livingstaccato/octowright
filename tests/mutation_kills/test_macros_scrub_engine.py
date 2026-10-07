# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Spellings a classified value takes in captured text, and the scrubber that must find each.

Every case is a real place a value is re-spelt before it reaches a recorder
row or a failure payload: JSON written without ASCII escaping, HTML text nodes
(which escape ``&`` but not ``"``), Python's repr inside a double-quoted
literal, ``repr(exc)`` of a locator error that quoted the value as JSON, and
Unicode case folding that no lower-casing reproduces (the long s, U+017F,
matches ``s`` ignoring case). A missing spelling is a
value left in the clear, so each test asserts the scrubbed text exactly.
"""

from __future__ import annotations

import json

import pytest

from octowright.macros.scrub_engine import REDACTED, filtered_text_scrubber, scrub_sensitive_values


def _both(text: str, values: tuple[str, ...], word_bounded: frozenset[str] = frozenset()) -> tuple[str, str]:
    """The sequential scrub and the filtered one, which must agree."""
    return (
        scrub_sensitive_values(text, values, word_bounded=word_bounded),
        filtered_text_scrubber(values, word_bounded)(text),
    )


def test_json_without_ascii_escaping_is_scrubbed() -> None:
    value = 'pä"ss'
    text = json.dumps({"pw": value}, ensure_ascii=False)

    assert text == '{"pw": "pä\\"ss"}'
    assert _both(text, (value,)) == ('{"pw": "<redacted>"}',) * 2


def test_repr_of_a_locator_error_quoting_the_value_as_json_is_scrubbed() -> None:
    value = 'pö"ss'
    message = f"waiting for get_by_text({json.dumps(value, ensure_ascii=False)})"
    text = repr(RuntimeError(message))

    assert text == 'RuntimeError(\'waiting for get_by_text("pö\\\\"ss")\')'
    assert _both(text, (value,)) == ("RuntimeError('waiting for get_by_text(\"<redacted>\")')",) * 2


def test_an_html_text_node_spelling_is_scrubbed() -> None:
    value = 'a&b"cd'
    text = '<p>a&amp;b"cd</p>'

    assert _both(text, (value,)) == ("<p><redacted></p>",) * 2


def test_repr_inside_a_double_quoted_literal_is_scrubbed() -> None:
    value = "p\x01'w1"
    text = repr("it's " + value)

    assert text == "\"it's p\\x01'w1\""
    assert _both(text, (value,)) == ('"it\'s <redacted>"',) * 2


@pytest.mark.parametrize("spelling", ["\u017fecret", "SECRET", "\u017fECRET"])
def test_unicode_case_folding_of_a_value_is_scrubbed(spelling: str) -> None:
    assert _both(f"value: {spelling}", ("secret",)) == (f"value: {REDACTED}",) * 2


def test_a_second_value_spelt_by_case_folding_is_scrubbed_after_a_first_replacement() -> None:
    text = "alpha1 then \u017fecret"

    assert _both(text, ("alpha1", "secret")) == (f"{REDACTED} then {REDACTED}",) * 2


class TestWordBounded:
    def test_a_value_starting_with_punctuation_needs_no_boundary_before_it(self) -> None:
        bounded = frozenset({"!ab"})

        assert _both("x!ab", ("!ab",), bounded) == (f"x{REDACTED}",) * 2

    def test_a_value_ending_with_punctuation_needs_no_boundary_after_it(self) -> None:
        bounded = frozenset({"ab!"})

        assert _both("ab!x", ("ab!",), bounded) == (f"{REDACTED}x",) * 2

    def test_a_value_inside_a_longer_identifier_is_left_alone(self) -> None:
        bounded = frozenset({"admin"})

        assert _both("myadmin admin_panel #admin-menu", ("admin",), bounded) == ("myadmin admin_panel #admin-menu",) * 2
        assert _both("log in as admin.", ("admin",), bounded) == (f"log in as {REDACTED}.",) * 2

    def test_a_short_punctuated_value_in_non_ascii_text_is_found_by_the_filter(self) -> None:
        # The filter must use the word-bounded patterns: under the short-value
        # rule a letter before ``!`` would hide this match from it.
        bounded = frozenset({"!ab"})

        assert _both("éa!ab", ("!ab",), bounded) == (f"éa{REDACTED}",) * 2
