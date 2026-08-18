# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Boundary tests for ``lint_urls._value_is_token_shaped``.

This replaced an entropy threshold that was wrong in both directions. The old
signal was ``len >= 20 and shannon_entropy > 4.0``; max entropy over a
16-symbol alphabet is exactly ``log2(16) == 4.0``, so against a strict ``>`` it
could NEVER fire on a hex digest, while a percent-encoded ``redirect_uri`` or a
human-readable ``title`` cleared it easily. (The previous version of this file
asserted the hex case returned False, locking the blind spot in as intent.)

The replacement is structural: a token is an unbroken run of alphanumerics
mixing letter and digit classes. Each test isolates ONE discriminator so a
fixed-length or fixed-alphabet input can't hide a broken threshold on another:

* the known-prefix signal, with shape deliberately kept NON-token-like, so the
  OR between the signals is load-bearing;
* the length threshold (>= 24), with the alphabet held constant;
* the punctuation rule, which is what keeps a UUID and a hyphenated slug out;
* the class-mix rule, which is what keeps a long word out.
"""

from __future__ import annotations

import pytest

from octowright.macros.lint_urls import _value_is_token_shaped


def test_known_prefix_counts_even_when_the_shape_alone_would_not() -> None:
    """A real leaked-token shape (prefix + a run of one repeated char) has no
    class mix at all past the prefix, so only the prefix signal can catch it.
    This also proves the OR (not AND) between the signals."""
    assert _value_is_token_shaped("ghp_" + "a" * 20) is True


def test_hex_digest_counts() -> None:
    """The exact case the old entropy threshold could not reach."""
    # The md5 of the empty string -- a fixture, not a key.
    assert _value_is_token_shaped("d41d8cd98f00b204e9800998ecf8427e") is True  # pragma: allowlist secret


@pytest.mark.parametrize(
    ("value", "expected", "why"),
    [
        ("a1" * 12, True, "24 chars, mixed classes -- exactly at the length threshold"),
        (("a1" * 12)[:23], False, "23 chars, same alphabet -- one below the threshold"),
        ("abcdefghijklmnopqrstuvwxyz", False, "26 letters, no digit: a word, not a token"),  # pragma: allowlist secret
        ("550e8400-e29b-41d4-a716-446655440000", False, "a UUID is punctuated, so it is an identifier"),
        ("summer-2026-launch-promo-email", False, "a hyphenated slug is not opaque"),
        ("Quarterly Business Review 2026", False, "spaces mean human text"),
        ("https%3A%2F%2Fclient.example.org", False, "percent-encoding means a nested URL"),
    ],
)
def test_shape_discriminators(value: str, expected: bool, why: str) -> None:
    assert _value_is_token_shaped(value) is expected, why


def test_length_threshold_is_evaluated_on_one_unchanging_alphabet() -> None:
    """Length is the ONLY thing that differs between these two inputs."""
    token = "a1" * 12
    assert len(token) == 24
    assert _value_is_token_shaped(token) is True
    assert _value_is_token_shaped(token[:23]) is False
