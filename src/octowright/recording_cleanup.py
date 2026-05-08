# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Recording-artefact cleanup.

Walks the recordings directory, classifies each file by suffix/path, and
returns/removes anything older than a caller-supplied cutoff. Pure logic —
the MCP tool wrapper lives in ``server/macros.py`` and the CLI command in
``cli.py``.

Classification rules:
  * ``.jsonl``                                  → ``recording``
  * ``.png``                                    → ``screenshot``
  * ``.zip``                                    → ``trace``
  * any file under ``<recordings_dir>/videos/`` → ``video``
  * any file under ``<recordings_dir>/downloads/`` → ``other``
  * everything else                             → ``other``

Per-file deletion (not recursive directory removal) — Playwright writes
videos into per-context subdirs, and we don't want one stale subdir to take
its sibling, still-fresh recordings down with it. After files are pruned,
empty subdirectories under ``videos/`` are best-effort removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class StaleFile:
    """A single artefact eligible for cleanup."""

    path: Path
    size_bytes: int
    age_days: float
    kind: str  # "recording" | "screenshot" | "video" | "trace" | "other"


def _classify(path: Path, recordings_dir: Path) -> str:
    """Decide which bucket this file falls into.

    Path-based rules win over suffix rules: a ``.png`` thumbnail dropped
    under ``videos/`` is still part of a video recording, not a screenshot.
    """
    try:
        rel = path.relative_to(recordings_dir)
    except ValueError:
        rel = None

    if rel is not None and rel.parts:
        top = rel.parts[0]
        if top == "videos":
            return "video"
        if top == "downloads":
            return "other"

    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "recording"
    if suffix == ".png":
        return "screenshot"
    if suffix == ".zip":
        return "trace"
    return "other"


def find_stale_files(
    recordings_dir: Path,
    days: float,
    *,
    now: datetime | None = None,
) -> list[StaleFile]:
    """Return all files under ``recordings_dir`` older than ``days``.

    Age is computed from mtime. The directory itself is skipped; subdirs are
    recursed. ``now`` is injectable so tests can pin "now" for deterministic
    age math.
    """
    if not recordings_dir.exists():
        return []

    reference = now if now is not None else datetime.now(UTC)
    cutoff_seconds = days * 86400.0

    stale: list[StaleFile] = []
    for path in recordings_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        age_seconds = reference.timestamp() - stat.st_mtime
        if age_seconds < cutoff_seconds:
            continue
        stale.append(
            StaleFile(
                path=path,
                size_bytes=stat.st_size,
                age_days=age_seconds / 86400.0,
                kind=_classify(path, recordings_dir),
            )
        )
    return stale


def cleanup_stale(
    stale: list[StaleFile],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete each entry in ``stale`` (unless ``dry_run``).

    Returns ``{removed_count, removed_bytes, errors, dry_run}`` where
    ``errors`` is a list of ``{"path": str, "error": str}`` entries — one
    failed unlink does not abort the rest of the sweep.

    After file deletion, empty directories under any ``videos/`` ancestor of
    a removed file are best-effort pruned. Failures here are silent: a
    non-empty sibling shouldn't surface as a user-visible error.
    """
    removed_count = 0
    removed_bytes = 0
    errors: list[dict[str, str]] = []
    touched_video_dirs: set[Path] = set()

    from octowright.http.session_artifacts import session_artifact_cache

    for entry in stale:
        if dry_run:
            continue
        try:
            entry.path.unlink()
        except OSError as exc:
            errors.append({"path": str(entry.path), "error": str(exc)})
            continue
        removed_count += 1
        removed_bytes += entry.size_bytes
        # Drop any cached artefact data keyed off this JSONL so a future
        # recording that happens to land at the same path can't see ghost rows.
        if entry.kind == "recording":
            session_artifact_cache.evict(entry.path)
        if entry.kind == "video":
            touched_video_dirs.add(entry.path.parent)

    # Best-effort empty-dir prune for video subdirs we touched. Walk up until
    # we leave the videos/ tree.
    for d in sorted(touched_video_dirs, key=lambda p: len(p.parts), reverse=True):
        cur: Path | None = d
        while cur is not None and cur.exists() and cur.name != "videos":
            try:
                next(cur.iterdir())
                break  # not empty — stop ascending
            except StopIteration:
                pass
            except OSError:
                break
            try:
                cur.rmdir()
            except OSError:
                break
            cur = cur.parent

    return {
        "removed_count": removed_count,
        "removed_bytes": removed_bytes,
        "errors": errors,
        "dry_run": dry_run,
    }
