# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.stabilize import STABILIZE_SCRIPT, render_stabilize_script


def test_stabilize_script_contains_date_now() -> None:
    assert "Date.now" in STABILIZE_SCRIPT


def test_stabilize_script_contains_raf() -> None:
    assert "requestAnimationFrame" in STABILIZE_SCRIPT


def test_stabilize_script_contains_animation_duration() -> None:
    assert "animation-duration: 0ms" in STABILIZE_SCRIPT


def test_stabilize_script_contains_transition_duration() -> None:
    assert "transition-duration: 0ms" in STABILIZE_SCRIPT


def test_render_stabilize_script_returns_string() -> None:
    result = render_stabilize_script()
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_stabilize_script_is_idempotent() -> None:
    assert render_stabilize_script() == render_stabilize_script()


def test_render_stabilize_script_matches_constant() -> None:
    assert render_stabilize_script() == STABILIZE_SCRIPT
