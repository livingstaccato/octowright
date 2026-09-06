# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Generate ``otto-mark.svg``: Otto drawn for small sizes.

Otto is a detailed mascot -- suckers, sparkles, blush, shading -- and below
roughly 64px those details stop being detail and become noise. Measured on a
1x display, the full drawing at 51px is a smudge; the same drawing with its
sub-threshold paths removed is legible. This is ordinary optical sizing: the
display drawing and the small drawing are different artwork, not the same
artwork at two scales.

Two properties of the traced file make this cheap and safe:

  * the whole dark line art is a SINGLE path (index 1), so its weight can be
    changed without touching anything else;
  * the trace emits structure first and detail last, so the paths worth
    dropping are a contiguous tail.

Neither is assumed here -- paths are selected by measured bounding-box area,
with the numbers recorded below -- but both are why the result is clean.

Run via scripts/generate_image_assets.sh, or directly:

    uv run --active python scripts/make_otto_mark.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "octowright" / "http" / "otto.svg"

#: Where the small-size mark is written. The UI copies are separate files rather
#: than one shared asset for the same reason otto.svg already is: these trees are
#: packaged and served independently.
TARGETS = (
    ROOT / "src" / "octowright" / "http" / "otto-mark.svg",
    ROOT / "packages" / "octowright-frontend" / "static" / "otto-mark.svg",
    ROOT / "demo" / "playground" / "static" / "otto-mark.svg",
    ROOT / "docs" / "images" / "otto" / "otto-mark.svg",
)

#: Drop a path whose bounding box covers less than this fraction of the artwork.
#: 0.002 was chosen by rendering the alternatives side by side at 51/64/90/160px:
#: it removes the 69 sucker/sparkle/blush paths while keeping the eyes and mouth,
#: which sit just above it. 0.01 also drops the face and is too far.
MIN_AREA_FRACTION = 0.002

#: Index of the single path holding the entire dark line art.
OUTLINE_PATH_INDEX = 1

#: The line art is near-black (#07111D), which at small sizes reads as a heavy
#: blot rather than a line. A dark teal keeps the shapes separated while letting
#: the mark hold together. Lowering the opacity instead was tried and rejected:
#: the colours underneath bleed through unevenly.
OUTLINE_COLOR = "#0f3f4a"

#: Coordinate precision. The trace emits six decimals of a 1178-unit canvas --
#: three orders of magnitude finer than a pixel at any size Otto is ever drawn.
COORD_DECIMALS = 1

_PATH_RE = re.compile(r"<path\b[^>]*/>", re.DOTALL)
_NUMBER_RE = re.compile(r"-?\d+\.\d+")
_FILL_RE = re.compile(r'fill="[^"]*"')
_BBOX_AREAS_HELP = """
Path areas are measured with getBBox in a real browser, which is the only
honest way to get them: a bounding box requires evaluating the path geometry.
The measured result for the current artwork is recorded in KEEP_INDEXES.
"""

#: Paths to keep, measured with getBBox against the current otto.svg (1178x927):
#: every path at or above MIN_AREA_FRACTION. Recorded rather than recomputed so
#: this script needs no browser; regenerate the list if the artwork is replaced.
KEEP_INDEXES = frozenset(range(0, 31)) | {35}


def _round_coords(path_markup: str) -> str:
    """Round every coordinate in a path to COORD_DECIMALS."""
    return _NUMBER_RE.sub(lambda m: f"{round(float(m.group()), COORD_DECIMALS):g}", path_markup)


def build_mark(source_svg: str) -> str:
    """Return the small-size mark derived from the full artwork."""
    paths = _PATH_RE.findall(source_svg)
    if not paths:
        raise SystemExit(f"no <path> elements found in {SOURCE}")

    header = source_svg[: source_svg.index("<path")]
    kept: list[str] = []
    for index, markup in enumerate(paths):
        if index not in KEEP_INDEXES:
            continue
        if index == OUTLINE_PATH_INDEX:
            markup = _FILL_RE.sub(f'fill="{OUTLINE_COLOR}"', markup, count=1)
        kept.append(_round_coords(markup))

    return header + "\n".join(kept) + "\n</svg>\n"


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    mark = build_mark(source)
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(mark, encoding="utf-8")
        print(f"wrote {target} ({len(mark) // 1024} KB, was {len(source) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
