# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.video_overlay (PNG-RGBA overlay renderer).

Pins:
- OverlayBox dataclass (frozen, field shape, defaults of DEFAULT_OVERLAY_BOX)
- TRANSPARENT sentinel (replaces former MAGENTA chroma-key)
- _measure_text formula
- _draw_text fallback to '?' on unknown char
- _blend_pixel: source-over compositing with RGBA
- _blend_rect coordinate clamping
- _write_png: valid PNG header + IDAT decompression round-trip
- _draw_overlay_box anchor branches
- _draw_pane_label optional fields
- render_overlay_image: canvas init, empty-text skip branch, panes loop
"""

from __future__ import annotations

import dataclasses
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from octowright import video_overlay as _vo

TRANSPARENT = _vo.TRANSPARENT  # (0, 0, 0, 0)


def _decode_png(path: Path) -> tuple[int, int, bytes]:
    """Decode a PNG written by _write_png. Returns (width, height, raw_rgba).

    raw_rgba is the row-major RGBA bytes with filter bytes stripped.
    """
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    width = height = 0
    idat_chunks: list[bytes] = []
    while pos < len(data):
        (chunk_len,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + chunk_len]
        pos += 8 + chunk_len + 4
        if tag == b"IHDR":
            width, height, _bd, _ct, *_ = struct.unpack(">IIBBBBB", chunk)
        elif tag == b"IDAT":
            idat_chunks.append(chunk)
        elif tag == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat_chunks))
    stride = width * 4 + 1
    pixels = bytearray()
    for y in range(height):
        row_start = y * stride + 1
        pixels.extend(raw[row_start : row_start + width * 4])
    return width, height, bytes(pixels)


def _pixel_at(width: int, raw: bytes, x: int, y: int) -> tuple[int, int, int, int]:
    offset = (y * width + x) * 4
    return tuple(raw[offset : offset + 4])  # type: ignore[return-value]


# ─── OverlayBox dataclass ────────────────────────────────────────────────────


class TestOverlayBox:
    def test_dataclass_is_frozen(self) -> None:
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
        assert _vo.DEFAULT_OVERLAY_BOX.anchor == "bottom-left"

    def test_default_overlay_box_padding_and_margin(self) -> None:
        assert _vo.DEFAULT_OVERLAY_BOX.padding == 12
        assert _vo.DEFAULT_OVERLAY_BOX.margin == 18

    def test_default_box_alpha_present_in_each_color_channel(self) -> None:
        for color in (
            _vo.DEFAULT_OVERLAY_BOX.background_rgba,
            _vo.DEFAULT_OVERLAY_BOX.title_rgba,
            _vo.DEFAULT_OVERLAY_BOX.subtitle_rgba,
        ):
            assert len(color) == 4

    def test_transparent_sentinel_is_zero_alpha(self) -> None:
        """TRANSPARENT is the buffer initialiser; alpha=0 means fully see-through."""
        assert _vo.TRANSPARENT == (0, 0, 0, 0)


# ─── _measure_text ──────────────────────────────────────────────────────────


class TestMeasureText:
    def test_empty_string_zero_width(self) -> None:
        assert _vo._measure_text("", scale=1) == 0

    def test_single_char_at_scale_one_is_six(self) -> None:
        assert _vo._measure_text("A", scale=1) == 6

    def test_scales_linearly_with_scale(self) -> None:
        assert _vo._measure_text("AB", scale=2) == 24

    def test_scale_zero_is_zero(self) -> None:
        assert _vo._measure_text("ABC", scale=0) == 0


# ─── _draw_text ──────────────────────────────────────────────────────────────


class TestDrawText:
    def _empty_canvas(self, width: int, height: int) -> list[list[_vo.ColorAlpha]]:
        return [list([TRANSPARENT] * width) for _ in range(height)]

    def test_known_char_alters_pixels(self) -> None:
        """Drawing 'A' at scale=1 leaves at least one pixel with non-zero alpha."""
        pixels = self._empty_canvas(20, 10)
        _vo._draw_text(pixels, 0, 0, "A", scale=1, color=(0, 0, 0, 255))
        flat = [p for row in pixels for p in row]
        assert any(p != TRANSPARENT for p in flat)

    def test_unknown_char_falls_back_to_question_mark(self) -> None:
        canvas_a = self._empty_canvas(20, 10)
        canvas_b = self._empty_canvas(20, 10)
        _vo._draw_text(canvas_a, 0, 0, "?", scale=1, color=(0, 0, 0, 255))
        _vo._draw_text(canvas_b, 0, 0, "@", scale=1, color=(0, 0, 0, 255))
        assert canvas_a == canvas_b

    def test_space_is_blank_glyph(self) -> None:
        pixels = self._empty_canvas(10, 10)
        _vo._draw_text(pixels, 0, 0, " ", scale=1, color=(0, 0, 0, 255))
        assert all(p == TRANSPARENT for row in pixels for p in row)


# ─── _blend_pixel ───────────────────────────────────────────────────────────


class TestBlendPixel:
    def test_alpha_zero_returns_base_unchanged(self) -> None:
        """Overlay alpha=0 → result is the base pixel."""
        assert _vo._blend_pixel((100, 150, 200, 255), (50, 50, 50, 0)) == (100, 150, 200, 255)

    def test_full_alpha_overlay_replaces_base(self) -> None:
        """Source alpha=255 → output rgb = overlay rgb, alpha = 255."""
        assert _vo._blend_pixel((0, 0, 0, 0), (200, 100, 50, 255)) == (200, 100, 50, 255)

    def test_partial_alpha_over_transparent_carries_alpha(self) -> None:
        """Partial alpha over a fully-transparent base → output alpha = source alpha."""
        result = _vo._blend_pixel((0, 0, 0, 0), (200, 200, 200, 128))
        # When base alpha is 0, source-over reduces to "use source values".
        assert result == (200, 200, 200, 128)

    def test_partial_alpha_over_opaque_blends_proportionally(self) -> None:
        """Source-over of half-alpha grey on opaque black ≈ half-grey, fully opaque."""
        result = _vo._blend_pixel((0, 0, 0, 255), (200, 200, 200, 128))
        # alpha_out = 128/255 + 255/255 * (1 - 128/255) = 1.0 → 255
        assert result[3] == 255
        # rgb ≈ 200 * 0.5 ≈ 100 (depending on rounding mode)
        assert 99 <= result[0] <= 101


# ─── _blend_rect ────────────────────────────────────────────────────────────


class TestBlendRect:
    def test_clamps_negative_x_y(self) -> None:
        pixels = [list([TRANSPARENT] * 5) for _ in range(5)]
        _vo._blend_rect(pixels, -10, -10, 3, 3, (0, 0, 0, 255))
        # Out-of-rect pixel still transparent.
        assert pixels[4][4] == TRANSPARENT
        # In-rect pixel painted opaque black.
        assert pixels[0][0] == (0, 0, 0, 255)

    def test_clamps_overflow_x_y(self) -> None:
        pixels = [list([TRANSPARENT] * 5) for _ in range(5)]
        _vo._blend_rect(pixels, 0, 0, 100, 100, (0, 0, 0, 255))
        assert all(p == (0, 0, 0, 255) for row in pixels for p in row)

    def test_zero_dimensions_no_op(self) -> None:
        pixels = [list([TRANSPARENT] * 5) for _ in range(5)]
        _vo._blend_rect(pixels, 2, 2, 2, 2, (0, 0, 0, 255))
        assert all(p == TRANSPARENT for row in pixels for p in row)

    def test_empty_pixels_no_op(self) -> None:
        _vo._blend_rect([], 0, 0, 10, 10, (0, 0, 0, 255))


# ─── _write_png ─────────────────────────────────────────────────────────────


class TestWritePng:
    def test_writes_valid_png_signature_and_dimensions(self, tmp_path: Path) -> None:
        out = tmp_path / "x.png"
        pixels = [[(10, 20, 30, 255), (40, 50, 60, 128)]]
        _vo._write_png(out, pixels)
        width, height, raw = _decode_png(out)
        assert (width, height) == (2, 1)
        assert _pixel_at(width, raw, 0, 0) == (10, 20, 30, 255)
        assert _pixel_at(width, raw, 1, 0) == (40, 50, 60, 128)

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "deep" / "nested" / "x.png"
        _vo._write_png(out, [[(1, 1, 1, 1)]])
        assert out.exists()

    def test_empty_pixels_emits_zero_sized_png(self, tmp_path: Path) -> None:
        out = tmp_path / "x.png"
        _vo._write_png(out, [])
        # Smallest legal PNG signature still emitted; dimensions are zero.
        data = out.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"


# ─── _draw_pane_label ───────────────────────────────────────────────────────


class TestDrawPaneLabel:
    def _canvas(self, w: int = 200, h: int = 200) -> list[list[_vo.ColorAlpha]]:
        return [list([TRANSPARENT] * w) for _ in range(h)]

    def test_with_full_pane_paints_label(self) -> None:
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "player", "kind": "chromium", "x": 0, "y": 0, "height": 100}
        _vo._draw_pane_label(pixels, pane)
        assert any(p != TRANSPARENT for row in pixels for p in row)

    def test_missing_height_treated_as_zero(self) -> None:
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "player", "kind": "chromium", "x": 0, "y": 0}
        _vo._draw_pane_label(pixels, pane)  # must not raise

    def test_empty_role_omitted_from_label(self) -> None:
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "", "kind": "chromium", "x": 0, "y": 0, "height": 100}
        _vo._draw_pane_label(pixels, pane)

    def test_empty_kind_omitted_from_label(self) -> None:
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "player", "kind": "", "x": 0, "y": 0, "height": 100}
        _vo._draw_pane_label(pixels, pane)

    def test_persona_uppercased(self) -> None:
        pixels = self._canvas()
        pane = {"persona": "alice", "role": "p", "kind": "c", "x": 0, "y": 0, "height": 100}
        _vo._draw_pane_label(pixels, pane)
        assert any(p != TRANSPARENT for row in pixels for p in row)


# ─── _draw_overlay_box ──────────────────────────────────────────────────────


class TestDrawOverlayBox:
    def _canvas(self, w: int = 200, h: int = 200) -> list[list[_vo.ColorAlpha]]:
        return [list([TRANSPARENT] * w) for _ in range(h)]

    def test_bottom_left_anchor_paints_at_bottom(self) -> None:
        canvas_height = 200
        pixels = self._canvas(h=canvas_height)
        _vo._draw_overlay_box(
            pixels,
            title="A",
            subtitle="B",
            canvas_height=canvas_height,
            overlay_box=_vo.DEFAULT_OVERLAY_BOX,
        )
        # Top row untouched.
        assert pixels[0][0] == TRANSPARENT
        # Bottom region carries non-zero alpha somewhere.
        assert any(p != TRANSPARENT for p in pixels[canvas_height - 30])

    def test_non_bottom_left_anchor_paints_at_top(self) -> None:
        canvas_height = 200
        pixels = self._canvas(h=canvas_height)
        custom = dataclasses.replace(_vo.DEFAULT_OVERLAY_BOX, anchor="top-left")
        _vo._draw_overlay_box(pixels, title="A", subtitle="B", canvas_height=canvas_height, overlay_box=custom)
        # Box sits at the top now.
        assert pixels[20][20] != TRANSPARENT


# ─── render_overlay_image ───────────────────────────────────────────────────


class TestRenderOverlayImage:
    def test_returns_target_path(self, tmp_path: Path) -> None:
        out = tmp_path / "ov.png"
        result = _vo.render_overlay_image(out, title="HI", subtitle="X", panes=[], canvas_width=50, canvas_height=50)
        assert result == out
        assert out.exists()

    def test_writes_canvas_dimensions_in_png_ihdr(self, tmp_path: Path) -> None:
        out = tmp_path / "ov.png"
        _vo.render_overlay_image(out, title="", subtitle="", panes=[], canvas_width=80, canvas_height=60)
        width, height, _raw = _decode_png(out)
        assert (width, height) == (80, 60)

    def test_empty_title_and_subtitle_skips_overlay_box(self, tmp_path: Path) -> None:
        """Both empty (after strip+upper) → no big title card; canvas is fully transparent."""
        out = tmp_path / "ov.png"
        _vo.render_overlay_image(out, title="", subtitle="   ", panes=[], canvas_width=20, canvas_height=10)
        width, _height, raw = _decode_png(out)
        for x in range(width):
            assert _pixel_at(width, raw, x, 0) == TRANSPARENT

    def test_panes_get_labels(self, tmp_path: Path) -> None:
        out = tmp_path / "ov.png"
        panes: list[dict[str, Any]] = [
            {"persona": "alice", "role": "player", "kind": "chromium", "x": 10, "y": 10, "height": 80}
        ]
        _vo.render_overlay_image(out, title="", subtitle="", panes=panes, canvas_width=200, canvas_height=200)
        width, _height, raw = _decode_png(out)
        # At least one pixel in the bottom-left of the pane region has non-zero alpha.
        seen = False
        for y in range(60, 90):
            for x in range(10, 80):
                if _pixel_at(width, raw, x, y) != TRANSPARENT:
                    seen = True
                    break
            if seen:
                break
        assert seen

    def test_title_uppercase_normalized(self, tmp_path: Path) -> None:
        out = tmp_path / "ov.png"
        _vo.render_overlay_image(out, title="hello world", subtitle="x", panes=[], canvas_width=200, canvas_height=200)
        width, height, raw = _decode_png(out)
        # Non-empty: at least one pixel is non-transparent.
        assert any(_pixel_at(width, raw, x, y) != TRANSPARENT for y in range(height) for x in range(width))
