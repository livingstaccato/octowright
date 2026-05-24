# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for ``octowright.macros.descriptions.describe_action``.

Used by the macro-status pill (browser_pool/visuals re-exports under the
legacy ``_describe_action`` name) and by the macro runtime to produce a
short, single-line summary of one action.
"""

from __future__ import annotations

from octowright.macros.descriptions import describe_action


def test_describe_action_falls_back_to_action_kind_when_no_hints() -> None:
    """No locator/value field → return the verb alone."""
    assert describe_action({"action": "wait_for"}) == "wait_for"


def test_describe_action_uses_question_mark_when_action_missing() -> None:
    """Defensive: a malformed action dict still produces a single-line label."""
    assert describe_action({}) == "?"


def test_describe_action_prefers_name_over_other_fields() -> None:
    """name wins over text/role/selector — the priority order is load-bearing."""
    out = describe_action({"action": "click", "name": "Sign in", "selector": "#x"})
    assert out == "click name=Sign in"


def test_describe_action_skips_empty_fields() -> None:
    """Empty strings, None, [] and {} are treated as "no hint" and skipped."""
    out = describe_action({"action": "click", "name": "", "text": None, "role": [], "selector": "#fallback"})
    assert out == "click selector=#fallback"


def test_describe_action_clips_long_values_with_ellipsis() -> None:
    """Long hints are clipped at 40 chars (39 + ellipsis) to fit the pill."""
    long = "x" * 100
    out = describe_action({"action": "navigate", "url": long})
    assert out.endswith("…")
    assert len(out.split("=", 1)[1]) == 40  # 39 chars + ellipsis


def test_visuals_re_export_is_same_callable() -> None:
    """The pool layer still imports ``_describe_action`` from visuals — make
    sure the re-export points at the same function so behaviour stays
    identical across both call sites."""
    from octowright.browser_pool.visuals import _describe_action

    assert _describe_action is describe_action
