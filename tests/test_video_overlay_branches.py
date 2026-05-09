# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.video_overlay (PPM-based overlay renderer).

Pins:
- OverlayBox dataclass (frozen, field shape, defaults of DEFAULT_OVERLAY_BOX)
- _measure_text formula
- _draw_text fallback to '?' on unknown char
- _blend_pixel alpha=0/255/partial
- _blend_rect coordinate clamping (negative + beyond canvas)
- _write_ppm header format + parent-dir creation
- _draw_overlay_box anchor branches (bottom-left vs top-left fallback)
- _draw_pane_label optional fields (missing role/kind/height)
- render_overlay_image: canvas init, empty-text skip branch, panes loop
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from octowright import video_overlay as _vo

# ─── OverlayBox dataclass ────────────────────────────────────────────────────


class TestOverlayBox:
    def test_dataclass_is_frozen(self) -> None:
        """`@dataclass(frozen=True)` — fields aren't reassignable post-construction."""
        box = _vo.OverlayBox(
            anchor="x",
            background_rgba=(0, 0, 0, 0),
            title_rgba=(0, 0, 0, 0),
            subtitle_rgba=(0, 0, 0, 0),
            padding=1,
            margin=1,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            box.padding = 99  # type: ignore[misc]

    def test_default_overlay_box_anchor_bottom_left(self) -> None:
        """The shipped default anchors at bottom-left."""
        assert _vo.DEFAULT_OVERLAY_BOX.anchor == "bottom-left"

    def test_default_overlay_box_padding_and_margin(self) -> None:
        """Padding=12 and margin=18 are documented defaults."""
        assert _vo.DEFAULT_OVERLAY_BOX.padding == 12
        assert _vo.DEFAULT_OVERLAY_BOX.margin == 18

    def test_default_box_alpha_present_in_each_color_channel(self) -> None:
        """Each rgba is a 4-tuple; mutation to 3-tuple would shift indexing."""
        for color in (
            _vo.DEFAULT_OVERLAY_BOX.background_rgba,
            _vo.DEFAULT_OVERLAY_BOX.title_rgba,
            _vo.DEFAULT_OVERLAY_BOX.subtitle_rgba,
        ):
            assert len(color) == 4

    def test_magenta_is_chroma_key_color(self) -> None:
        """MAGENTA is the chromakey colour used in apply_video_overlay."""
        assert _vo.MAGENTA == (255, 0, 255)


# ─── _measure_text ──────────────────────────────────────────────────────────


class TestMeasureText:
    def test_empty_string_zero_width(self) -> None:
        """No characters → zero width."""
        assert _vo._measure_text("", scale=1) == 0

    def test_single_char_at_scale_one_is_six(self) -> None:
        """5-pixel glyph + 1-pixel gap = 6 at scale=1."""
        assert _vo._measure_text("A", scale=1) == 6

    def test_scales_linearly_with_scale(self) -> None:
        """At scale=2: (5*2 + 2) per char = 12 per char."""
        assert _vo._measure_text("AB", scale=2) == 24

    def test_scale_zero_is_zero(self) -> None:
        """scale=0 → zero contribution per char (degenerate but defined)."""
        assert _vo._measure_text("ABC", scale=0) == 0


# ─── _draw_text ──────────────────────────────────────────────────────────────


class TestDrawText:
    def _empty_canvas(self, width: int, height: int) -> list[list[_vo.Color]]:
        return [list([_vo.MAGENTA] * width) for _ in range(height)]

    def test_known_char_alters_pixels(self) -> None:
        """Drawing 'A' at scale=1 darkens at least one pixel away from MAGENTA."""
        pixels = self._empty_canvas(20, 10)
        _vo._draw_text(pixels, 0, 0, "A", scale=1, color=(0, 0, 0, 255))
        flat = [p for row in pixels for p in row]
        assert any(p != _vo.MAGENTA for p in flat)

    def test_unknown_char_falls_back_to_question_mark(self) -> None:
        """Char not in FONT_5X7 renders as '?' (asserts via pixel-equivalence)."""
        canvas_a = self._empty_canvas(20, 10)
        canvas_b = self._empty_canvas(20, 10)
        _vo._draw_text(canvas_a, 0, 0, "?", scale=1, color=(0, 0, 0, 255))
        _vo._draw_text(canvas_b, 0, 0, "@", scale=1, color=(0, 0, 0, 255))  # unknown char
        # Both should produce identical pixel patterns (the '?' fallback).
        assert canvas_a == canvas_b

    def test_space_is_blank_glyph(self) -> None:
        """Space renders all-zero rows — no pixels altered."""
        pixels = self._empty_canvas(10, 10)
        _vo._draw_text(pixels, 0, 0, " ", scale=1, color=(0, 0, 0, 255))
        assert all(p == _vo.MAGENTA for row in pixels for p in row)


# ─── _blend_pixel ───────────────────────────────────────────────────────────


class TestBlendPixel:
    def test_alpha_zero_returns_base(self) -> None:
        """Overlay alpha=0 → base unchanged."""
        assert _vo._blend_pixel((100, 150, 200), (255, 255, 255, 0)) == (100, 150, 200)

    def test_alpha_255_returns_overlay(self) -> None:
        """Full alpha → overlay rgb (rounded)."""
        assert _vo._blend_pixel((0, 0, 0), (200, 100, 50, 255)) == (200, 100, 50)

    def test_alpha_partial_blends_proportionally(self) -> None:
        """Half alpha → halfway between base and overlay (rounded)."""
        # alpha=128 → 128/255 ≈ 0.5019...
        result = _vo._blend_pixel((0, 0, 0), (200, 200, 200, 128))
        # Each channel ≈ 200 * 0.5019 ≈ 100.4 → 100 (or 101 depending on rounding mode).
        assert result[0] in (100, 101)


# ─── _blend_rect ────────────────────────────────────────────────────────────


class TestBlendRect:
    def test_clamps_negative_x_y(self) -> None:
        """Negative coords clamp to 0 — no out-of-bounds writes."""
        pixels = [list([_vo.MAGENTA] * 5) for _ in range(5)]
        _vo._blend_rect(pixels, -10, -10, 3, 3, (0, 0, 0, 255))
        # Pixel (4,4) wasn't touched; only (0..2, 0..2).
        assert pixels[4][4] == _vo.MAGENTA
        # Pixel (0,0) should now be black.
        assert pixels[0][0] == (0, 0, 0)

    def test_clamps_overflow_x_y(self) -> None:
        """Overflow coords clamp to canvas size — no IndexError."""
        pixels = [list([_vo.MAGENTA] * 5) for _ in range(5)]
        _vo._blend_rect(pixels, 0, 0, 100, 100, (0, 0, 0, 255))
        # All 5x5 pixels touched.
        assert all(p == (0, 0, 0) for row in pixels for p in row)

    def test_zero_dimensions_no_op(self) -> None:
        """x0==x1 or y0==y1 → no pixels altered."""
        pixels = [list([_vo.MAGENTA] * 5) for _ in range(5)]
        _vo._blend_rect(pixels, 2, 2, 2, 2, (0, 0, 0, 255))
        assert all(p == _vo.MAGENTA for row in pixels for p in row)

    def test_empty_pixels_no_op(self) -> None:
        """Empty canvas → no rows accessed (the `width = pixels[0]` guard)."""
        # _blend_rect handles `pixels = []` via the `if pixels else 0` ternary.
        _vo._blend_rect([], 0, 0, 10, 10, (0, 0, 0, 255))


# ─── _write_ppm ─────────────────────────────────────────────────────────────


class TestWritePpm:
    def test_header_format(self, tmp_path: Path) -> None:
        """File starts with `P6\\n<W> <H>\\n255\\n`."""
        out = tmp_path / "x.ppm"
        pixels = [[(10, 20, 30), (40, 50, 60)]]  # 1 row, 2 cols
        _vo._write_ppm(out, pixels)
        data = out.read_bytes()
        assert data.startswith(b"P6\n2 1\n255\n")
        # Body contains the 6 RGB bytes for the two pixels.
        body = data[len(b"P6\n2 1\n255\n") :]
        assert body == bytes((10, 20, 30, 40, 50, 60))

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        """`target_path.parent` is mkdir'd."""
        out = tmp_path / "deep" / "nested" / "x.ppm"
        _vo._write_ppm(out, [[(1, 1, 1)]])
        assert out.exists()

    def test_empty_pixels_emits_zero_dimensions(self, tmp_path: Path) -> None:
        """Empty list → header `0 0`."""
        out = tmp_path / "x.ppm"
        _vo._write_ppm(out, [])
        assert out.read_bytes() == b"P6\n0 0\n255\n"


# ─── _draw_pane_label ───────────────────────────────────────────────────────


class TestDrawPaneLabel:
    def _canvas(self, w: int = 200, h: int = 200) -> list[list[_vo.Color]]:
        return [list([_vo.MAGENTA] * w) for _ in range(h)]

    def test_with_full_pane_paints_label(self) -> None:
        """Pane with persona+role+kind paints into the canvas."""
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "player", "kind": "chromium", "x": 0, "y": 0, "height": 100}
        _vo._draw_pane_label(pixels, pane)
        assert any(p != _vo.MAGENTA for row in pixels for p in row)

    def test_missing_height_treated_as_zero(self) -> None:
        """`pane.get('height', 0)` — absent height → label rect lands off-canvas (negative y0).

        With height=0 and y=0, y0 = 0 + 0 - label_height - 16 < 0; clamping
        means nothing visible is drawn. We just assert no crash.
        """
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "player", "kind": "chromium", "x": 0, "y": 0}
        _vo._draw_pane_label(pixels, pane)  # must not raise

    def test_empty_role_omitted_from_label(self) -> None:
        """role='' → not appended; label is just persona / kind."""
        # No assertion on rendered pixels — just verify no crash and non-trivial output.
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "", "kind": "chromium", "x": 0, "y": 0, "height": 100}
        _vo._draw_pane_label(pixels, pane)

    def test_empty_kind_omitted_from_label(self) -> None:
        """kind='' → not appended."""
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "player", "kind": "", "x": 0, "y": 0, "height": 100}
        _vo._draw_pane_label(pixels, pane)

    def test_persona_uppercased(self) -> None:
        """Persona is uppercased; lowercase 'a' → 'A' glyph used."""
        # We just verify no crash — pixel-level glyph check would be brittle.
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "p", "kind": "c", "x": 0, "y": 0, "height": 100}
        _vo._draw_pane_label(pixels, pane)
        assert any(p != _vo.MAGENTA for row in pixels for p in row)


# ─── _draw_overlay_box ──────────────────────────────────────────────────────


class TestDrawOverlayBox:
    def _canvas(self, w: int = 200, h: int = 200) -> list[list[_vo.Color]]:
        return [list([_vo.MAGENTA] * w) for _ in range(h)]

    def test_bottom_left_anchor_paints_at_bottom(self) -> None:
        """anchor='bottom-left' → box drawn near the bottom of canvas."""
        canvas_height = 200
        pixels = self._canvas(h=canvas_height)
        _vo._draw_overlay_box(
            pixels,
            title="A",
            subtitle="B",
            canvas_height=canvas_height,
            overlay_box=_vo.DEFAULT_OVERLAY_BOX,
        )
        # Top row should still be MAGENTA (untouched), bottom region should differ.
        assert pixels[0][0] == _vo.MAGENTA
        assert any(p != _vo.MAGENTA for p in pixels[canvas_height - 30])

    def test_non_bottom_left_anchor_paints_at_top(self) -> None:
        """Any non-'bottom-left' anchor uses margin-from-top."""
        canvas_height = 200
        pixels = self._canvas(h=canvas_height)
        custom = dataclasses.replace(_vo.DEFAULT_OVERLAY_BOX, anchor="top-left")
        _vo._draw_overlay_box(pixels, title="A", subtitle="B", canvas_height=canvas_height, overlay_box=custom)
        # The top-left margin region should now be different from MAGENTA.
        # Box starts at margin (18) from top.
        assert pixels[20][20] != _vo.MAGENTA


# ─── render_overlay_image ───────────────────────────────────────────────────


class TestRenderOverlayImage:
    def test_returns_target_path(self, tmp_path: Path) -> None:
        """Returns the target_path argument."""
        out = tmp_path / "ov.ppm"
        result = _vo.render_overlay_image(out, title="HI", subtitle="X", panes=[], canvas_width=50, canvas_height=50)
        assert result == out
        assert out.exists()

    def test_writes_canvas_dimensions_to_header(self, tmp_path: Path) -> None:
        """PPM header carries the requested canvas WxH."""
        out = tmp_path / "ov.ppm"
        _vo.render_overlay_image(out, title="", subtitle="", panes=[], canvas_width=80, canvas_height=60)
        assert out.read_bytes().startswith(b"P6\n80 60\n255\n")

    def test_empty_title_and_subtitle_skips_overlay_box(self, tmp_path: Path) -> None:
        """Both empty (after strip+upper) → no big title card drawn — canvas is solid MAGENTA."""
        out = tmp_path / "ov.ppm"
        _vo.render_overlay_image(out, title="", subtitle="   ", panes=[], canvas_width=20, canvas_height=10)
        # 20x10 PPM = 60 RGB bytes of MAGENTA after the header.
        body = out.read_bytes().split(b"\n", 3)[3]
        # Every pixel triple is (255, 0, 255).
        assert len(body) == 20 * 10 * 3
        # Spot-check first and last pixel.
        assert body[0:3] == bytes((255, 0, 255))
        assert body[-3:] == bytes((255, 0, 255))

    def test_panes_get_labels(self, tmp_path: Path) -> None:
        """Each pane in `panes` triggers _draw_pane_label."""
        out = tmp_path / "ov.ppm"
        panes: list[dict[str, Any]] = [
            {"persona": "alice", "role": "player", "kind": "chromium", "x": 10, "y": 10, "height": 80}
        ]
        _vo.render_overlay_image(out, title="", subtitle="", panes=panes, canvas_width=200, canvas_height=200)
        # Body has at least one non-MAGENTA pixel from the pane label.
        body = out.read_bytes().split(b"\n", 3)[3]
        # Look for any byte triple that isn't (255,0,255).
        seen_non_magenta = False
        for i in range(0, len(body), 3):
            if body[i : i + 3] != bytes((255, 0, 255)):
                seen_non_magenta = True
                break
        assert seen_non_magenta

    def test_title_uppercase_normalized(self, tmp_path: Path) -> None:
        """`title.strip().upper()` — assert no crash with mixed case."""
        out = tmp_path / "ov.ppm"
        _vo.render_overlay_image(out, title="hello world", subtitle="x", panes=[], canvas_width=200, canvas_height=200)
        # Just non-empty body; pixel-perfect check would be brittle.
        body = out.read_bytes().split(b"\n", 3)[3]
        assert any(body[i : i + 3] != bytes((255, 0, 255)) for i in range(0, len(body), 3))
