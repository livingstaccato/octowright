# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pins the single-source-of-truth ``DEFAULT_PREVIEW_CHARS`` constant.

Before consolidation the constant lived in three places with two different
values:

- ``session.core``         → 4000
- ``session.core_ops_mixin``→ 4000 (shadow)
- ``captures``             → 2000 (intentionally different)

These tests guard against accidental drift: any future re-introduction of a
divergent value would silently regress the diagnostic-bundle preview cap.
"""

from __future__ import annotations


def test_default_preview_chars_value() -> None:
    """The session-level preview cap is the documented 4000-char ceiling."""
    from octowright.session._constants import DEFAULT_PREVIEW_CHARS

    assert DEFAULT_PREVIEW_CHARS == 4000


def test_default_preview_chars_single_source_in_session_namespace() -> None:
    """Every session-package re-export resolves to the same singleton object."""
    from octowright.session import DEFAULT_PREVIEW_CHARS as via_pkg
    from octowright.session._constants import DEFAULT_PREVIEW_CHARS as via_constants
    from octowright.session.core import DEFAULT_PREVIEW_CHARS as via_core
    from octowright.session.core_ops_mixin import DEFAULT_PREVIEW_CHARS as via_ops

    # Integer literals are interned only up to 256 in CPython; equality is the
    # contract that matters here. The point of the test is "all four names
    # resolve to the same value", not "they are the same int instance".
    assert via_pkg == via_core == via_ops == via_constants == 4000


def test_capture_preview_chars_renamed_and_distinct() -> None:
    """The capture-specific preview cap kept its 2000-char value and got a
    distinct name so a future grep for ``DEFAULT_PREVIEW_CHARS`` won't pull
    in the unrelated capture surface."""
    from octowright import captures

    assert captures.CAPTURE_PREVIEW_CHARS == 2000
    # The old shadow name must be gone — leaving it would resurrect the
    # ambiguity the rename fixed.
    assert not hasattr(captures, "DEFAULT_PREVIEW_CHARS")
