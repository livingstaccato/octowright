# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Sidecar-filename allowlist for recording deletion.

Lives outside ``routes/sessions.py`` so the LOC ceiling there isn't
inflated by the comment block enumerating every legitimate producer.
"""

from __future__ import annotations

import re

# Sidecar filenames legitimately produced next to a recording's JSONL. The
# full set is:
#   * ``{stem}.jsonl``                  — the main recording (the file itself)
#   * ``{stem}.markdown.md``            — session/core.py:_markdown_cache_path
#   * ``{stem}.websocket.jsonl``        — session/core.py:_websocket_cache_path
#   * ``{stem}.har`` / ``{stem}.<n>.har`` — browser_pool/launch_helpers.py
#     (initial HAR plus rotation suffixes from ``next_har_path``)
#   * ``{stem}.trace.zip``              — session/core_ops_mixin.py
#   * ``{stem}.webm``                   — Playwright-managed video output
#   * ``{stem}.console.index.json``     — http/session_artifacts.py sidecar
#   * ``{stem}.downloads.index.json``   — http/session_artifacts.py sidecar
#   * ``{stem}.png`` / ``{stem}.<n>.png`` — explicit captures + index variants
#
# A plain ``startswith(stem)`` filter is too broad: two recordings with
# overlapping stem prefixes (e.g. ``abc`` and ``abcde``) drag each other's
# sidecars into deletion. Match the suffix after ``stem`` against this
# allowlist instead.
RECORDING_SIDECAR_SUFFIXES: frozenset[str] = frozenset(
    {
        ".jsonl",
        ".markdown.md",
        ".websocket.jsonl",
        ".har",
        ".trace.zip",
        ".webm",
        ".console.index.json",
        ".downloads.index.json",
        ".png",
    }
)

# Rotated HAR (``foo.har`` -> ``foo.1.har``) and indexed screenshot
# (``foo.0.png``) siblings.
RECORDING_SIDECAR_ROTATIONS: re.Pattern[str] = re.compile(r"^\.\d+\.(har|png)$")


def is_recording_sidecar(filename: str, stem: str) -> bool:
    if not filename.startswith(stem):
        return False
    tail = filename[len(stem) :]
    if tail in RECORDING_SIDECAR_SUFFIXES:
        return True
    return RECORDING_SIDECAR_ROTATIONS.match(tail) is not None
