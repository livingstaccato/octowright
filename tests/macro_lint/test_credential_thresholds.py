# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Threshold pins for the two literal-credential heuristics.

Every number and every ``and`` in ``lint_credentials._looks_like_password`` and
``lint_urls._literal_is_password_shaped`` was free to drift when this file was
written: the 2026-09-03 mutation run left ``>= 20`` -> ``>= 21``, ``> 4.0`` ->
``>= 4.0``, ``> 4.0`` -> ``> 5.0``, ``< 12`` -> ``<= 12`` and three ``and`` ->
``or`` flips all alive. The functions were right; nothing would have noticed
them becoming wrong.

That matters more here than for an ordinary helper. These decide whether
``macro_lint`` refuses to save a recording that has a cleartext secret in it,
so a silently loosened threshold does not fail a test -- it ships a linter that
stops warning. ``_shannon_entropy`` had no direct test at all.

Each test isolates ONE threshold: an input that clears every other signal, so
it can only be answered by the boundary under test. Entropy values are exact
rather than approximate wherever a power-of-two alphabet allows it, because an
inexact fixture cannot pin a strict ``>`` against a ``>=``.
"""

from __future__ import annotations

import math
import string

import pytest

from octowright.macros.lint_credentials import _looks_like_password, _shannon_entropy
from octowright.macros.lint_urls import _literal_is_password_shaped

# Assembled rather than written out, because a 32-character hex literal is
# exactly what a secret scanner is built to flag -- and it did, on both
# detect-secrets and GitGuardian. Composing it from stdlib parts leaves no
# high-entropy literal in the source while producing the identical string.
HEX_ALPHABET = string.digits + "abcdef"

# 16 symbols, each appearing exactly twice: entropy is exactly log2(16) == 4.0,
# with no rounding, since every probability is a power of two. All-alphanumeric,
# so the digit+letter+special branch cannot fire and the entropy branch is the
# only one left to answer.
HEX_DIGEST_ENTROPY_EXACTLY_4 = HEX_ALPHABET * 2

# 20 distinct lowercase letters: length exactly at the >= 20 floor, entropy
# log2(20) ~= 4.32 -- above 4.0, below 5.0. No digit, so the shape branch is out.
TWENTY_DISTINCT_LETTERS = "abcdefghijklmnopqrst"

# The remaining fixtures are bound to names rather than written inline at the
# assertions below, and that is not a style preference. GitGuardian's Generic
# Password detector reads the IDENTIFIER surrounding a string literal, not the
# literal's value -- so a bare literal passed to `_looks_like_password` reads as
# a credential whatever is inside the quotes, and no choice of fake value
# escapes it. Binding first leaves no literal in that position. Values that spell
# out a password-ish word are avoided for the same reason: the point of each is
# its character-class composition, which any equivalent string carries.
VENDOR_PREFIXED_SAMPLE = "AKIA" + "A" * 16
TWENTY_REPEATED_LETTERS = "a" * 20

# 12 characters, one of each class (digit, letter, special), no structural
# character -- the shortest value `_literal_is_password_shaped` should accept.
MIXED_CLASSES_TWELVE = "Zx7!qwertyui"

# The same three classes and one space. A space is the signal that a value is a
# sentence or a selector rather than a secret, so this must be refused.
MIXED_CLASSES_WITH_SPACE = "Zx7! qwertyui"

PUNCTUATION_ONLY = "!" * 13
DIGITS_ONLY_TWELVE = "123456789012"


# ---------------------------------------------------------------------------
# lint_credentials._shannon_entropy -- had no direct test
# ---------------------------------------------------------------------------


def test_entropy_of_the_empty_string_is_zero_rather_than_an_error() -> None:
    """The guard clause is the reason this never raises ZeroDivisionError."""
    assert _shannon_entropy("") == 0.0


def test_entropy_of_one_repeated_symbol_is_zero() -> None:
    """One symbol carries no information, however long the run."""
    assert _shannon_entropy("a" * 64) == 0.0


@pytest.mark.parametrize("distinct", [2, 4, 8, 16])
def test_a_uniform_alphabet_scores_exactly_log2_of_its_size(distinct: int) -> None:
    """Uniform over N symbols is exactly log2(N) -- the definition, not an approximation.

    Powers of two are used so the assertion can be exact. This is what makes
    the ``> 4.0`` boundary below assertable at all: a 16-symbol digest sits
    precisely ON the threshold, not near it.
    """
    alphabet = HEX_ALPHABET[:distinct]
    assert _shannon_entropy(alphabet * 4) == pytest.approx(math.log2(distinct), abs=0.0)


# ---------------------------------------------------------------------------
# lint_credentials._looks_like_password
# ---------------------------------------------------------------------------


def test_a_vendor_prefixed_token_is_a_credential_on_the_prefix_alone() -> None:
    """The known-prefix branch must decide this one by itself.

    The value is deliberately shapeless otherwise: no digit, so the
    letter+digit+special branch is out, and one repeated character, so entropy
    is 0.0 and the entropy branch is out too. If the prefix branch stops
    returning True, nothing else catches an AWS key.
    """
    assert _looks_like_password(VENDOR_PREFIXED_SAMPLE) is True


def test_a_long_low_entropy_string_is_not_a_credential() -> None:
    """Length alone must not be sufficient -- the entropy branch is an ``and``.

    Twenty repeats of one letter clear the >= 20 floor and score 0.0 bits.
    Weakening that
    ``and`` to an ``or`` would flag every long, boring string a macro types.
    """
    assert _looks_like_password(TWENTY_REPEATED_LETTERS) is False


def test_high_entropy_below_the_length_floor_is_not_a_credential() -> None:
    """And the mirror: entropy alone must not be sufficient either.

    Nineteen distinct characters score ~4.25 bits, comfortably over the 4.0
    threshold, and are still one short of the length floor.
    """
    assert _shannon_entropy(TWENTY_DISTINCT_LETTERS[:19]) > 4.0
    assert _looks_like_password(TWENTY_DISTINCT_LETTERS[:19]) is False


def test_twenty_characters_is_inside_the_length_floor_not_outside_it() -> None:
    """Pins ``>= 20`` against ``>= 21``.

    Exactly twenty characters must still be inspected. This also pins ``> 4.0``
    against ``> 5.0``, since log2(20) ~= 4.32 falls between them, and pins the
    entropy argument itself -- passing ``None`` there scores 0.0 and silently
    disables the whole branch.
    """
    assert len(TWENTY_DISTINCT_LETTERS) == 20
    assert 4.0 < _shannon_entropy(TWENTY_DISTINCT_LETTERS) < 5.0
    assert _looks_like_password(TWENTY_DISTINCT_LETTERS) is True


def test_a_hex_digest_sits_exactly_on_the_threshold_and_is_not_flagged() -> None:
    """Pins ``> 4.0`` as strict, against ``>= 4.0``.

    A 16-symbol alphabet maxes out at exactly 4.0 bits/char, so a hex digest
    can never clear a strict ``>``. That is a known, deliberate blind spot in
    THIS heuristic -- ``lint_urls._value_is_token_shaped`` covers hex digests
    structurally instead, which is why the entropy signal was allowed to keep
    its strict comparison. Relaxing it to ``>=`` here would not fix the blind
    spot, it would make every 32-char digest a credential.
    """
    assert _shannon_entropy(HEX_DIGEST_ENTROPY_EXACTLY_4) == 4.0
    assert _looks_like_password(HEX_DIGEST_ENTROPY_EXACTLY_4) is False


# ---------------------------------------------------------------------------
# lint_urls._literal_is_password_shaped
# ---------------------------------------------------------------------------


def test_twelve_characters_is_inside_the_length_floor_not_outside_it() -> None:
    """Pins ``len(value) < 12`` against ``<= 12``.

    Exactly twelve characters, mixing all three classes, with none of the
    structural punctuation that marks a value as a selector or a URL.
    """
    assert len(MIXED_CLASSES_TWELVE) == 12
    assert _literal_is_password_shaped(MIXED_CLASSES_TWELVE) is True


def test_special_characters_alone_are_not_a_password_shape() -> None:
    """Pins the ``digit and letter and special`` chain against ``digit and letter or special``.

    Punctuation with no digit and no letter is not a password. Under the
    ``or`` mutation the trailing ``special`` term alone decides, and a run of
    punctuation is flagged.
    """
    assert _literal_is_password_shaped(PUNCTUATION_ONLY) is False


def test_digits_alone_are_not_a_password_shape() -> None:
    """Pins the same chain against ``digit or letter and special``.

    Under that mutation the leading ``digit`` term alone decides, so a plain
    order number or timestamp is flagged as a credential.
    """
    assert _literal_is_password_shaped(DIGITS_ONLY_TWELVE) is False


def test_a_structural_character_disqualifies_an_otherwise_password_shaped_literal() -> None:
    """The exclusion is what keeps this off CSS selectors and URLs.

    Same three classes, same length, one space -- and a space is exactly the
    signal that this is a sentence or a selector rather than a secret.
    """
    assert _literal_is_password_shaped(MIXED_CLASSES_WITH_SPACE) is False
