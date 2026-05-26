#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGES_DIR="${ROOT_DIR}/docs/images"
BRAND_DIR="${IMAGES_DIR}/brand"
OTTO_DIR="${IMAGES_DIR}/otto"
FAVICON_DIR="${IMAGES_DIR}/favicon"

# Sources of truth — square masters. Banner = branded mark (with text);
# otto.svg = vector mascot. Both live under their category directories.
LOGO_SRC="${BRAND_DIR}/octowright-banner.png"
OTTO_SRC_SVG="${OTTO_DIR}/otto.svg"

if [[ ! -f "${LOGO_SRC}" ]]; then
  echo "missing source image: ${LOGO_SRC}" >&2
  exit 1
fi

if [[ ! -f "${OTTO_SRC_SVG}" ]]; then
  echo "missing otto source image: ${OTTO_SRC_SVG}" >&2
  exit 1
fi

resize_from() {
  local src="$1"
  local size="$2"
  local out="$3"

  # sips (macOS) is fast for PNG/JPEG inputs but cannot rasterize SVG.
  # For SVG sources we need ImageMagick (or rsvg-convert if available).
  local is_svg=0
  case "${src##*.}" in
    svg|SVG) is_svg=1 ;;
  esac

  if [[ ${is_svg} -eq 0 ]] && command -v sips >/dev/null 2>&1; then
    sips -z "${size}" "${size}" "${src}" --out "${out}" >/dev/null
    return 0
  fi
  if command -v rsvg-convert >/dev/null 2>&1 && [[ ${is_svg} -eq 1 ]]; then
    rsvg-convert -w "${size}" -h "${size}" "${src}" -o "${out}"
    return 0
  fi
  if command -v magick >/dev/null 2>&1; then
    magick -background none "${src}" -resize "${size}x${size}" "${out}"
    return 0
  fi
  if command -v convert >/dev/null 2>&1; then
    convert -background none "${src}" -resize "${size}x${size}" "${out}"
    return 0
  fi

  echo "missing image resizer: install ImageMagick (magick/convert) or rsvg-convert, or run on macOS with PNG sources via sips" >&2
  exit 1
}

# --- Octowright logo ladder -------------------------------------------------
mkdir -p "${BRAND_DIR}"
for size in 128 256 512; do
  out="${BRAND_DIR}/octowright-logo-${size}.png"
  resize_from "${LOGO_SRC}" "${size}" "${out}"
  echo "wrote ${out}"
done

# --- Otto avatar ladder -----------------------------------------------------
mkdir -p "${OTTO_DIR}"
for size in 64 128 256 512; do
  out="${OTTO_DIR}/otto-avatar-${size}.png"
  resize_from "${OTTO_SRC_SVG}" "${size}" "${out}"
  echo "wrote ${out}"
done

# --- Favicon derivatives ----------------------------------------------------
mkdir -p "${FAVICON_DIR}"
resize_from "${OTTO_SRC_SVG}" 192 "${FAVICON_DIR}/favicon-icon-192.png"
echo "wrote ${FAVICON_DIR}/favicon-icon-192.png"
resize_from "${OTTO_SRC_SVG}" 512 "${FAVICON_DIR}/favicon-icon-512.png"
echo "wrote ${FAVICON_DIR}/favicon-icon-512.png"
resize_from "${OTTO_SRC_SVG}" 180 "${FAVICON_DIR}/apple-touch-icon.png"
echo "wrote ${FAVICON_DIR}/apple-touch-icon.png"

echo "note: ${FAVICON_DIR}/social-og-image.png and ${FAVICON_DIR}/favicon.ico are intentionally manual/non-direct assets."
