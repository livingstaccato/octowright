# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import personas as _personas
from .defaults import PROFILES_DIR, SUPPORTED_KINDS


def profile_dir(kind: str, name: str) -> Path:
    """Engine-profile directory for (kind, persona). Public name preserved;
    internally routes through personas.engine_profile_dir."""
    return _personas.engine_profile_dir(persona=name, kind=kind)


def list_profiles(kind: str | None = None) -> list[dict[str, Any]]:
    """List all engine profiles. Each entry: {kind, name, path, size_bytes, mtime, last_used}.
    `name` is the persona name."""
    if not PROFILES_DIR.exists():
        return []
    kinds = [kind] if kind else list(SUPPORTED_KINDS)
    out: list[dict[str, Any]] = []
    for persona_entry in PROFILES_DIR.iterdir():
        if not persona_entry.is_dir():
            continue
        for k in kinds:
            engine_dir = persona_entry / k
            if not engine_dir.is_dir():
                continue
            stat = engine_dir.stat()
            size = sum(f.stat().st_size for f in engine_dir.rglob("*") if f.is_file())
            out.append(
                {
                    "kind": k,
                    "name": persona_entry.name,
                    "path": str(engine_dir),
                    "size_bytes": size,
                    "mtime": stat.st_mtime,
                    "last_used": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat().replace("+00:00", "Z"),
                }
            )
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out


def delete_profile(kind: str, name: str) -> Path:
    """Delete a single engine-profile directory. Raises FileNotFoundError."""
    target = profile_dir(kind, name)
    if not target.exists():
        raise FileNotFoundError(f"no engine profile at {target}; list saved profiles with `profile_list`")
    shutil.rmtree(target)
    return target


def delete_persona(name: str) -> Path:
    """Delete an entire persona directory (all engine profiles + metadata)."""
    target = _personas.persona_dir(name)
    if not target.exists():
        raise FileNotFoundError(f"no persona at {target}; list personas with `persona_list`")
    shutil.rmtree(target)
    return target
