# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The public variant set a DOM redaction pass needs before a screenshot.

``sensitive_value_variants`` is the multi-value form of ``_serialized_variants``.
A caller redacting a live page has to remove every encoding of every classified
value and then assert nothing remains, so it needs one flat set across all of
them rather than a tuple per value.
"""

from __future__ import annotations

from octowright.macros.privacy import (
    _serialized_variants,
    sensitive_value_variants,
)


def test_the_set_is_exactly_the_union_of_each_value_s_variants() -> None:
    """No value may be dropped and none invented: a missed spelling stays rendered."""
    first, second = "p@ss w/rd", "a b"

    variants = sensitive_value_variants((first, second))

    assert set(variants) == set(_serialized_variants(first)) | set(_serialized_variants(second))


def test_longer_variants_come_first() -> None:
    """Load-bearing for a replacing caller, not cosmetic.

    Percent-encoding routinely makes one variant a substring of another, because
    `quote` leaves an already-safe value unchanged. A caller replacing the shorter
    match first consumes the characters the longer match needed and leaves the
    remainder of the longer spelling on the page.
    """
    # Two values chosen so lexicographic order and longest-first DISAGREE. With a
    # single encoding-heavy value they coincide -- its longest spellings also sort
    # first -- and the assertion passes under a plain `sorted()`, which is what a
    # mutation of the key proved before this input was picked.
    variants = sensitive_value_variants(("a b", "zzzzzzzzzz"))

    assert len(variants) > 1, "these values must encode into several spellings"
    lengths = [len(variant) for variant in variants]
    assert lengths == sorted(lengths, reverse=True), f"variants are not longest-first: {variants}"
    assert lengths != [len(variant) for variant in sorted(variants)], (
        "this fixture no longer distinguishes longest-first from lexicographic"
    )


def test_non_string_entries_are_skipped_rather_than_raising() -> None:
    """Macro arguments carry null and numeric fields as a matter of course.

    `_serialized_variants` raises TypeError on a non-string, so the caller would
    lose the whole redaction pass -- and a redaction pass that raises is a
    screenshot that never gets taken, not a leak, but it stops the capture dead.

    The empty string needs no guard of its own: `_serialized_variants` already
    yields nothing for it. Asserted here so a future guard is not added back as
    though it were load-bearing.
    """
    variants = sensitive_value_variants(("", None, 0, "kept"))  # type: ignore[arg-type]

    assert "" not in variants
    assert variants == _serialized_variants("kept")
    assert _serialized_variants("") == ()


def test_no_values_is_an_empty_set_not_a_failure() -> None:
    """A macro with nothing classified still runs the redaction path."""
    assert sensitive_value_variants(()) == ()
