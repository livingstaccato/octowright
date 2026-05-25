#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGES_DIR="${ROOT_DIR}/docs/images"
LOGO_SRC="${IMAGES_DIR}/octowright-banner.png"
OTTO_SRC_PNG="${IMAGES_DIR}/otto-only.png"
OTTO_SRC_SVG="${IMAGES_DIR}/otto.svg"

if [[ ! -f "${LOGO_SRC}" ]]; then
  echo "missing source image: ${LOGO_SRC}" >&2
  exit 1
fi

if [[ ! -f "${OTTO_SRC_SVG}" && ! -f "${OTTO_SRC_PNG}" ]]; then
  echo "missing otto source image: ${OTTO_SRC_SVG} or ${OTTO_SRC_PNG}" >&2
  exit 1
fi

resize_from() {
  local src="$1"
  local size="$2"
  local out="$3"

  if command -v sips >/dev/null 2>&1; then
    sips -z "${size}" "${size}" "${src}" --out "${out}" >/dev/null
    return 0
  fi
  if command -v magick >/dev/null 2>&1; then
    magick "${src}" -resize "${size}x${size}" "${out}"
    return 0
  fi
  if command -v convert >/dev/null 2>&1; then
    convert "${src}" -resize "${size}x${size}" "${out}"
    return 0
  fi

  echo "missing image resizer: install ImageMagick (magick/convert) or run on macOS with sips" >&2
  exit 1
}

# --- Octowright logo ladder -------------------------------------------------
for size in 128 256 512; do
  out="${IMAGES_DIR}/octowright-logo-${size}.png"
  resize_from "${LOGO_SRC}" "${size}" "${out}"
  echo "wrote ${out}"
done

# --- Otto avatar ladder -----------------------------------------------------
# Prefer SVG as source-of-truth vector; PNG fallback if needed.
OTTO_SRC="${OTTO_SRC_SVG}"
if [[ ! -f "${OTTO_SRC}" ]]; then
  OTTO_SRC="${OTTO_SRC_PNG}"
fi

mkdir -p "${IMAGES_DIR}/otto"
for size in 64 128 256 512; do
  out="${IMAGES_DIR}/otto/otto-avatar-${size}.png"
  resize_from "${OTTO_SRC}" "${size}" "${out}"
  echo "wrote ${out}"
done

# --- Favicon derivatives ----------------------------------------------------
mkdir -p "${IMAGES_DIR}/favicon"
resize_from "${OTTO_SRC}" 192 "${IMAGES_DIR}/favicon/favicon-icon-192.png"
echo "wrote ${IMAGES_DIR}/favicon/favicon-icon-192.png"
resize_from "${OTTO_SRC}" 512 "${IMAGES_DIR}/favicon/favicon-icon-512.png"
echo "wrote ${IMAGES_DIR}/favicon/favicon-icon-512.png"
resize_from "${OTTO_SRC}" 180 "${IMAGES_DIR}/favicon/apple-touch-icon.png"
echo "wrote ${IMAGES_DIR}/favicon/apple-touch-icon.png"

echo "note: ${IMAGES_DIR}/favicon/social-og-image.png and ${IMAGES_DIR}/favicon/favicon.ico are intentionally manual/non-direct assets."
