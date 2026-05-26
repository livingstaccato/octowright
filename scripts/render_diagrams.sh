#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Render all PlantUML files under docs/architecture/ to SVG, and
# optionally to PNG for offline review.
#
# Usage:
#   scripts/render_diagrams.sh [SOURCE_DIR] [--png]
#
# SVG is always emitted next to the .puml source. With --png, also
# rasterise each SVG to a 2400-px-wide PNG in /tmp/octowright-diagrams/
# (override with OCTOWRIGHT_DIAGRAM_PNG_DIR=...). PNG width is set by
# OCTOWRIGHT_DIAGRAM_PNG_WIDTH (default 2400) — bump it for diagrams
# with many lifelines / packages that don't fit at the default.
#
# Requires `plantuml` on PATH (brew install plantuml). PNG mode also
# requires `rsvg-convert` (brew install librsvg). PlantUML in turn
# requires Java 8+; the diagrams here only use core language features.

set -euo pipefail

SOURCE_DIR="${1:-docs/architecture}"
EMIT_PNG=0
for arg in "${@:2}"; do
    case "$arg" in
        --png) EMIT_PNG=1 ;;
        *) echo "warn: unknown arg '$arg' ignored" >&2 ;;
    esac
done

PNG_DIR="${OCTOWRIGHT_DIAGRAM_PNG_DIR:-/tmp/octowright-diagrams}"
PNG_WIDTH="${OCTOWRIGHT_DIAGRAM_PNG_WIDTH:-2400}"

if ! command -v plantuml >/dev/null 2>&1; then
    echo "error: plantuml not found on PATH" >&2
    echo "install with: brew install plantuml" >&2
    exit 1
fi

shopt -s nullglob
files=("$SOURCE_DIR"/*.puml)
if [ ${#files[@]} -eq 0 ]; then
    echo "no .puml files in $SOURCE_DIR" >&2
    exit 0
fi

echo "rendering ${#files[@]} diagram(s) under $SOURCE_DIR/ to SVG..."
plantuml -tsvg -nometadata -progress "${files[@]}"
echo "done. svg outputs in $SOURCE_DIR/:"
ls "$SOURCE_DIR"/*.svg

if [ "$EMIT_PNG" -eq 1 ]; then
    if ! command -v rsvg-convert >/dev/null 2>&1; then
        echo "error: --png requested but rsvg-convert not found on PATH" >&2
        echo "install with: brew install librsvg" >&2
        exit 1
    fi
    mkdir -p "$PNG_DIR"
    echo "rasterising svg → png at ${PNG_WIDTH}px into $PNG_DIR/ ..."
    for svg in "$SOURCE_DIR"/*.svg; do
        name="$(basename "$svg" .svg)"
        rsvg-convert -w "$PNG_WIDTH" "$svg" -o "$PNG_DIR/$name.png"
    done
    ls "$PNG_DIR"/*.png
fi
