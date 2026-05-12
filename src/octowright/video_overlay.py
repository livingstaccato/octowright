# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Color = tuple[int, int, int]
ColorAlpha = tuple[int, int, int, int]

# Fully transparent base. The overlay PNG carries alpha straight to ffmpeg's
# overlay filter, so we no longer need a chroma-key sentinel colour.
TRANSPARENT: ColorAlpha = (0, 0, 0, 0)


@dataclass(frozen=True)
class OverlayBox:
    anchor: str
    background_rgba: ColorAlpha
    title_rgba: ColorAlpha
    subtitle_rgba: ColorAlpha
    padding: int
    margin: int


FONT_5X7: dict[str, tuple[str, ...]] = {
    " ": ("00000",) * 7,
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "/": ("00001", "00010", "00100", "01000", "10000", "00000", "00000"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10011", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00001", "00001", "00001", "00001", "10001", "10001", "01110"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "10001", "11001", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "?": ("11110", "00001", "00010", "00100", "00100", "00000", "00100"),
}

DEFAULT_OVERLAY_BOX = OverlayBox(
    anchor="bottom-left",
    # Low-key dark pill with real alpha. Pre-PNG (PPM + chroma-key), low
    # alphas blended with magenta producing the hot-pink labels users
    # complained about. With proper RGBA PNG output the alpha is now the
    # alpha you actually see in the composited video.
    background_rgba=(18, 22, 30, 130),
    title_rgba=(232, 235, 240, 220),
    subtitle_rgba=(200, 206, 216, 200),
    padding=12,
    margin=18,
)


def render_overlay_image(
    target_path: Path,
    *,
    title: str,
    subtitle: str,
    panes: list[dict[str, Any]],
    canvas_width: int,
    canvas_height: int,
) -> Path:
    pixels = [list([TRANSPARENT] * canvas_width) for _ in range(canvas_height)]
    normalized_title = title.strip().upper()
    normalized_subtitle = subtitle.strip().upper()
    if normalized_title or normalized_subtitle:
        _draw_overlay_box(
            pixels,
            title=normalized_title,
            subtitle=normalized_subtitle,
            canvas_height=canvas_height,
            overlay_box=DEFAULT_OVERLAY_BOX,
        )
    for pane in panes:
        _draw_pane_label(pixels, pane)
    _write_png(target_path, pixels)
    return target_path


def _draw_pane_label(pixels: list[list[ColorAlpha]], pane: dict[str, Any]) -> None:
    label_parts = [str(pane["persona"]).upper()]
    role = str(pane["role"]).upper()
    kind = str(pane["kind"]).upper()
    if role:
        label_parts.append(role)
    if kind:
        label_parts.append(kind)
    label = " / ".join(label_parts)
    scale = 1
    padding = 8
    width = _measure_text(label, scale=scale) + (padding * 2)
    height = (7 * scale) + (padding * 2)
    x0 = int(pane["x"]) + 16
    y0 = int(pane["y"]) + int(pane.get("height", 0)) - height - 16
    _blend_rect(pixels, x0, y0, x0 + width, y0 + height, DEFAULT_OVERLAY_BOX.background_rgba)
    _draw_text(
        pixels,
        x0 + padding,
        y0 + padding,
        label,
        scale=scale,
        color=DEFAULT_OVERLAY_BOX.subtitle_rgba,
    )


def _draw_overlay_box(
    pixels: list[list[ColorAlpha]],
    *,
    title: str,
    subtitle: str,
    canvas_height: int,
    overlay_box: OverlayBox,
) -> None:
    title_scale = 2
    subtitle_scale = 1
    line_gap = 8
    title_width = _measure_text(title, scale=title_scale)
    subtitle_width = _measure_text(subtitle, scale=subtitle_scale)
    box_width = max(title_width, subtitle_width) + (overlay_box.padding * 2)
    box_height = (7 * title_scale) + (7 * subtitle_scale) + (overlay_box.padding * 2) + line_gap
    x0 = overlay_box.margin
    y0 = canvas_height - overlay_box.margin - box_height if overlay_box.anchor == "bottom-left" else overlay_box.margin
    _blend_rect(pixels, x0, y0, x0 + box_width, y0 + box_height, overlay_box.background_rgba)
    _draw_text(
        pixels,
        x0 + overlay_box.padding,
        y0 + overlay_box.padding,
        title,
        scale=title_scale,
        color=overlay_box.title_rgba,
    )
    _draw_text(
        pixels,
        x0 + overlay_box.padding,
        y0 + overlay_box.padding + (7 * title_scale) + line_gap,
        subtitle,
        scale=subtitle_scale,
        color=overlay_box.subtitle_rgba,
    )


def _measure_text(text: str, *, scale: int) -> int:
    return sum((5 * scale) + scale for _ in text)


def _draw_text(pixels: list[list[ColorAlpha]], x: int, y: int, text: str, *, scale: int, color: ColorAlpha) -> None:
    cursor = x
    for char in text:
        glyph = FONT_5X7.get(char, FONT_5X7["?"])
        for row, pattern in enumerate(glyph):
            for col, bit in enumerate(pattern):
                if bit != "1":
                    continue
                _blend_rect(
                    pixels,
                    cursor + (col * scale),
                    y + (row * scale),
                    cursor + ((col + 1) * scale),
                    y + ((row + 1) * scale),
                    color,
                )
        cursor += (5 * scale) + scale


def _blend_rect(pixels: list[list[ColorAlpha]], x0: int, y0: int, x1: int, y1: int, color: ColorAlpha) -> None:
    height = len(pixels)
    width = len(pixels[0]) if pixels else 0
    left = max(0, x0)
    top = max(0, y0)
    right = min(width, x1)
    bottom = min(height, y1)
    for row in range(top, bottom):
        line = pixels[row]
        for col in range(left, right):
            line[col] = _blend_pixel(line[col], color)


def _blend_pixel(base: ColorAlpha, overlay: ColorAlpha) -> ColorAlpha:
    """Standard "source over" RGBA compositing. Pre-multiplied output alpha
    lets stacked semi-transparent rects (label rect + text on top) combine
    correctly, and the final PNG carries that alpha to ffmpeg's overlay
    filter which composites it on the video natively."""
    src_a = overlay[3] / 255.0
    dst_a = base[3] / 255.0
    out_a = src_a + dst_a * (1.0 - src_a)
    if out_a <= 0.0:
        return (0, 0, 0, 0)
    inv = 1.0 - src_a
    r = round(((overlay[0] * src_a) + (base[0] * dst_a * inv)) / out_a)
    g = round(((overlay[1] * src_a) + (base[1] * dst_a * inv)) / out_a)
    b = round(((overlay[2] * src_a) + (base[2] * dst_a * inv)) / out_a)
    return (r, g, b, round(out_a * 255))


def _write_png(target_path: Path, pixels: list[list[ColorAlpha]]) -> None:
    """Write an RGBA PNG using stdlib only (zlib + struct).

    The format is the simplest legal PNG: a single IHDR (8-bit RGBA), one
    IDAT with filter-type 0 (no filter) per scanline, and an IEND. Good
    enough for an overlay sprite — no Pillow needed.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    height = len(pixels)
    width = len(pixels[0]) if pixels else 0

    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type: None
        for r, g, b, a in row:
            raw.extend((r, g, b, a))

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit, RGBA
    idat = zlib.compress(bytes(raw), 6)

    with target_path.open("wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(_chunk(b"IHDR", ihdr))
        handle.write(_chunk(b"IDAT", idat))
        handle.write(_chunk(b"IEND", b""))
