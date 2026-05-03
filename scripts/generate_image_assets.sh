#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT_DIR}/docs/images/octowright-banner.png"

if [[ ! -f "${SRC}" ]]; then
  echo "missing source image: ${SRC}" >&2
  exit 1
fi

resize_one() {
  local size="$1"
  local out="$2"

  if command -v sips >/dev/null 2>&1; then
    sips -z "${size}" "${size}" "${SRC}" --out "${out}" >/dev/null
    return 0
  fi
  if command -v magick >/dev/null 2>&1; then
    magick "${SRC}" -resize "${size}x${size}" "${out}"
    return 0
  fi
  if command -v convert >/dev/null 2>&1; then
    convert "${SRC}" -resize "${size}x${size}" "${out}"
    return 0
  fi

  echo "missing image resizer: install ImageMagick (magick/convert) or run on macOS with sips" >&2
  exit 1
}

for size in 128 256 512 1024; do
  out="${ROOT_DIR}/docs/images/octowright-logo-${size}.png"
  resize_one "${size}" "${out}"
  echo "wrote ${out}"
done
