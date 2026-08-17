# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Boundary tests for ``_token_like`` (the ``_looks_like_password`` heuristic
minus its URL-inappropriate rule 2 -- see ``lint.py`` for why).

Each test isolates ONE of the two remaining signals so a fixed-length or
fixed-entropy input can't hide a broken threshold on the other:

* the token-prefix signal, with entropy deliberately kept LOW so the OR
  between the two signals is load-bearing (a known token prefix must count
  even when the rest of the heuristic wouldn't fire on its own);
* the length threshold (>= 20), with entropy held constant and HIGH so only
  the length comparison changes between cases;
* the entropy threshold (> 4.0, strict), with length held constant and >= 20
  so only the entropy comparison changes -- including the exact value 4.0,
  which the `>` (not `>=`) must reject.
"""

from __future__ import annotations

from octowright.macros.lint import _token_like


def test_known_prefix_counts_even_with_low_entropy() -> None:
    """A real leaked-token SHAPE (prefix + run of one repeated char) has
    entropy nowhere near 4.0 -- only the prefix signal can catch it, so this
    also proves the OR (not AND) between the two signals."""
    assert _token_like("ghp_" + "a" * 20) is True


def test_no_prefix_and_low_entropy_short_string_is_not_token_like() -> None:
    assert _token_like("plain-url-path") is False


def test_length_boundary_at_exactly_twenty_with_high_entropy_counts() -> None:
    s = "0123456789abcdefghij"  # 20 distinct chars, len 20, entropy > 4.0
    assert len(s) == 20
    assert _token_like(s) is True


def test_length_one_below_boundary_with_same_high_entropy_does_not_count() -> None:
    s = "0123456789abcdefghij"[:19]  # same alphabet, one char short
    assert len(s) == 19
    assert _token_like(s) is False


def test_entropy_exactly_at_threshold_does_not_count() -> None:
    """16 distinct chars over 32 positions (each twice) -> entropy == 4.0
    exactly. The threshold is a strict `>`, so this must NOT count."""
    s = "0123456789abcdef" * 2  # pragma: allowlist secret
    assert len(s) == 32
    assert _token_like(s) is False


def test_entropy_just_above_threshold_counts() -> None:
    s = "0123456789abcdefghijklmnopqrstuv"  # pragma: allowlist secret -- 32 distinct chars, entropy 5.0
    assert len(s) == 32
    assert _token_like(s) is True


def test_entropy_below_threshold_with_sufficient_length_does_not_count() -> None:
    s = "01234567" * 3  # 8 distinct chars, len 24, entropy 3.0
    assert len(s) == 24
    assert _token_like(s) is False
