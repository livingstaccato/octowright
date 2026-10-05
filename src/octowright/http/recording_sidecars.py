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
import shutil
from pathlib import Path

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


# The sidecars that share the recording's own ``.jsonl`` suffix, derived from
# the allowlist above so a new one cannot be added in one place and missed in
# the other. A ``*.jsonl`` glob matches them too, and every reader that walks
# the recordings root for sessions has to skip them.
_JSONL_SIDECAR_SUFFIXES: tuple[str, ...] = tuple(
    sorted(s for s in RECORDING_SIDECAR_SUFFIXES if s != ".jsonl" and s.endswith(".jsonl"))
)


def is_session_recording(filename: str) -> bool:
    """Whether ``filename`` is a session's main JSONL rather than a sidecar.

    Compared casefolded, so a case-insensitive filesystem that hands back a
    ``.WebSocket.JSONL`` spelling classifies the same as Linux does.
    """
    folded = filename.casefold()
    return folded.endswith(".jsonl") and not folded.endswith(_JSONL_SIDECAR_SUFFIXES)


def is_recording_sidecar(filename: str, stem: str) -> bool:
    if not filename.startswith(stem):
        return False
    tail = filename[len(stem) :]
    if tail in RECORDING_SIDECAR_SUFFIXES:
        return True
    return RECORDING_SIDECAR_ROTATIONS.match(tail) is not None


def is_failure_dump(filename: str, session_id: str) -> bool:
    """``{instance_id}-fail-{ts}.html`` / ``.png`` from ``core_ops_mixin``'s diagnostic bundle."""
    return (
        filename.startswith(f"{session_id}-fail-")
        and filename.endswith((".html", ".png"))
        and "/" not in filename
        and "\\" not in filename
    )


def session_artifact_dirs(root: Path, session_id: str, stem: str) -> list[Path]:
    """Per-session directories under the recordings root, by producer convention.

    * ``videos/{stem}/``           -- ``launch_helpers._build_video_kwargs``
    * ``downloads/{instance_id}/`` -- ``session/downloads.save_download``
    * ``.frame-cache/{instance_id}/`` -- ``http/routes/media._frame_cache_path``
    """
    return [root / "videos" / stem, root / "downloads" / session_id, root / ".frame-cache" / session_id]


def remove_contained_dir(candidate: Path, root: Path) -> bool:
    """Remove *candidate* when it lies under *root*; ``True`` if something was removed.

    A symlink is unlinked, never followed: a same-user link planted as
    ``downloads/<id>`` must not turn a delete into an rmtree of its target.
    Otherwise the resolved path must stay inside the resolved root, so a
    ``..`` or a symlinked PARENT cannot walk the delete out of it.
    """
    if candidate.is_symlink():
        candidate.unlink()
        return True
    if not candidate.is_dir():
        return False
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        return False
    shutil.rmtree(resolved)
    return True
