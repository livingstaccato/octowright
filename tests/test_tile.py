# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Window-tiling helpers — pure math, no Playwright launches needed."""

from __future__ import annotations

from octowright.pool import _tile_args_for_chromium, _tile_position


def test_tile_position_index_zero_is_top_left() -> None:
    x, y, w, h = _tile_position(0)
    assert (x, y) == (40, 60)
    assert (w, h) == (720, 540)


def test_tile_position_index_one_steps_right_in_columns() -> None:
    x0, _, _, _ = _tile_position(0)
    x1, y1, _, _ = _tile_position(1)
    assert x1 > x0
    assert y1 == 60  # same row


def test_tile_position_wraps_to_next_row_at_column_count() -> None:
    """4 cols by default → index 4 starts a new row."""
    _, y0, _, _ = _tile_position(0)
    _, y4, _, _ = _tile_position(4)
    assert y4 > y0
    assert _tile_position(4)[0] == _tile_position(0)[0]  # same x as col 0


def test_tile_position_is_deterministic() -> None:
    """Same index always yields the same slot — needed for muscle memory."""
    assert _tile_position(7) == _tile_position(7)


def test_tile_args_emits_chromium_window_flags() -> None:
    args = _tile_args_for_chromium(0)
    assert any(a.startswith("--window-position=") for a in args)
    assert any(a.startswith("--window-size=") for a in args)


def test_tile_args_index_changes_position_value() -> None:
    a0 = next(a for a in _tile_args_for_chromium(0) if a.startswith("--window-position="))
    a1 = next(a for a in _tile_args_for_chromium(1) if a.startswith("--window-position="))
    assert a0 != a1
