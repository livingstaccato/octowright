# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Sweep orphaned profile dirs under PROFILES_DIR.

Counterpart to ``recording_cleanup``: now that ``browser_launch(label=X)``
auto-promotes to a persistent profile, casual labels accumulate on disk. A
typical Chromium user-data-dir is 10-50 MB; ten orphan labels = half a GB.

A profile dir is "stale" when:

* its mtime is older than ``days`` days, AND
* it is not the user_data_dir of any currently-live BrowserSession.

The live-session check is delegated by the caller (the MCP tool / CLI) since
the pool is shared singleton state — this module stays pure / testable.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

log = get_logger(__name__)


@dataclass
class StaleProfile:
    persona: str
    engine: str
    path: Path
    size_bytes: int
    age_days: float


def _dir_size_bytes(path: Path) -> int:
    """Recursive sum — best-effort, ignores files that disappear mid-walk."""
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def find_stale_profiles(
    profiles_root: Path,
    days: float,
    in_use: Iterable[Path] = (),
) -> list[StaleProfile]:
    """Return per-engine profile dirs older than ``days`` and not in ``in_use``.

    A profile lives at ``profiles_root/<persona>/<engine>/`` (post-personas
    layout). Each engine subdir is treated as an independent unit so you can
    age out chromium without touching the firefox of the same persona.
    """
    if not profiles_root.exists():
        return []
    cutoff = time.time() - (days * 86400)
    in_use_resolved = {p.resolve() for p in in_use}
    out: list[StaleProfile] = []
    for persona_dir in profiles_root.iterdir():
        if not persona_dir.is_dir():
            continue
        for engine_dir in persona_dir.iterdir():
            if not engine_dir.is_dir():
                continue
            try:
                resolved = engine_dir.resolve()
            except OSError:
                continue
            if resolved in in_use_resolved:
                continue
            try:
                mtime = engine_dir.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                continue
            out.append(
                StaleProfile(
                    persona=persona_dir.name,
                    engine=engine_dir.name,
                    path=engine_dir,
                    size_bytes=_dir_size_bytes(engine_dir),
                    age_days=(time.time() - mtime) / 86400,
                )
            )
    return out


def cleanup_stale(stale: list[StaleProfile], dry_run: bool = True) -> dict[str, Any]:
    """Remove the listed profile dirs (or report what would be removed).

    Returns ``{removed_count, removed_bytes, errors}`` where ``errors`` is a
    list of ``{"path": str, "error": str}`` entries — one failed rmtree does
    not abort the rest of the sweep, but the failure is visible to callers
    (and the MCP tool surface) rather than silently swallowed.
    """
    removed_count = 0
    removed_bytes = 0
    errors: list[dict[str, str]] = []
    if dry_run:
        return {"removed_count": 0, "removed_bytes": 0, "errors": errors}
    for sp in stale:
        try:
            shutil.rmtree(sp.path)
        except OSError as exc:
            errors.append({"path": str(sp.path), "error": str(exc)})
            log.warning(
                "profile_cleanup.rmtree_failed",
                path=str(sp.path),
                error=str(exc),
            )
            continue
        removed_count += 1
        removed_bytes += sp.size_bytes
        # Best-effort prune of the now-empty persona dir; failures here are
        # genuinely uninteresting (a non-empty sibling shouldn't surface as
        # a user-visible error), so keep silent-swallow per policy.
        parent = sp.path.parent
        if parent.exists() and not any(parent.iterdir()):
            try:
                parent.rmdir()
            except OSError:
                pass
    return {"removed_count": removed_count, "removed_bytes": removed_bytes, "errors": errors}
