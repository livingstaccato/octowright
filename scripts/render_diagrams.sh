#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
# Render all PlantUML files under docs/architecture/ to SVG.
# Usage: scripts/render_diagrams.sh [docs/architecture]
#
# Requires `plantuml` on PATH (brew install plantuml). PlantUML in turn
# requires Java 8+; the diagrams here only use core language features.

set -euo pipefail

SOURCE_DIR="${1:-docs/architecture}"

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
