# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from typing import Any

Color = tuple[int, int, int]

MAGENTA: Color = (255, 0, 255)
BLACK: Color = (14, 20, 28)
WHITE: Color = (255, 255, 255)
SOFT_WHITE: Color = (230, 235, 240)

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


def render_overlay_image(
    target_path: Path,
    *,
    title: str,
    subtitle: str,
    panes: list[dict[str, Any]],
    canvas_width: int,
    canvas_height: int,
) -> Path:
    pixels = [list([MAGENTA] * canvas_width) for _ in range(canvas_height)]
    _fill_rect(pixels, 20, 18, canvas_width - 20, 108, BLACK)
    _draw_text(pixels, 40, 28, title.upper(), scale=4, color=WHITE)
    _draw_text(pixels, 40, 76, subtitle.upper(), scale=2, color=SOFT_WHITE)
    for pane in panes:
        _draw_pane_label(pixels, pane)
    footer = f"{canvas_width}X{canvas_height} WEBSITE HERO EXPORT"
    footer_width = _measure_text(footer, scale=1)
    _draw_text(pixels, canvas_width - footer_width - 32, canvas_height - 24, footer, scale=1, color=SOFT_WHITE)
    _write_ppm(target_path, pixels)
    return target_path


def _draw_pane_label(pixels: list[list[Color]], pane: dict[str, Any]) -> None:
    label = f"{pane['persona']}/{pane['role']}/{pane['kind']}".upper()
    x = int(pane["x"]) + 18
    y = int(pane["y"]) + 18
    width = _measure_text(label, scale=2) + 20
    _fill_rect(pixels, x - 8, y - 8, x + width, y + 22, BLACK)
    _draw_text(pixels, x, y, label, scale=2, color=WHITE)


def _measure_text(text: str, *, scale: int) -> int:
    return sum((5 * scale) + scale for _ in text)


def _draw_text(pixels: list[list[Color]], x: int, y: int, text: str, *, scale: int, color: Color) -> None:
    cursor = x
    for char in text:
        glyph = FONT_5X7.get(char, FONT_5X7["?"])
        for row, pattern in enumerate(glyph):
            for col, bit in enumerate(pattern):
                if bit != "1":
                    continue
                _fill_rect(
                    pixels,
                    cursor + (col * scale),
                    y + (row * scale),
                    cursor + ((col + 1) * scale),
                    y + ((row + 1) * scale),
                    color,
                )
        cursor += (5 * scale) + scale


def _fill_rect(pixels: list[list[Color]], x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
    height = len(pixels)
    width = len(pixels[0]) if pixels else 0
    left = max(0, x0)
    top = max(0, y0)
    right = min(width, x1)
    bottom = min(height, y1)
    for row in range(top, bottom):
        line = pixels[row]
        for col in range(left, right):
            line[col] = color


def _write_ppm(target_path: Path, pixels: list[list[Color]]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    height = len(pixels)
    width = len(pixels[0]) if pixels else 0
    with target_path.open("wb") as handle:
        handle.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
        for row in pixels:
            for red, green, blue in row:
                handle.write(bytes((red, green, blue)))
