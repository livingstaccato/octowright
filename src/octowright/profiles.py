from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .defaults import PROFILES_DIR, SUPPORTED_KINDS

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(name: str) -> str:
    cleaned = _SLUG_RE.sub("-", name.strip())
    cleaned = cleaned.strip("-.")
    if not cleaned:
        raise ValueError(f"profile name {name!r} produced an empty slug")
    return cleaned


def profile_dir(kind: str, name: str) -> Path:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {kind!r}")
    return PROFILES_DIR / kind / _slug(name)


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def list_profiles(kind: str | None = None) -> list[dict[str, Any]]:
    if not PROFILES_DIR.exists():
        return []
    kinds = [kind] if kind else list(SUPPORTED_KINDS)
    out: list[dict[str, Any]] = []
    for k in kinds:
        root = PROFILES_DIR / k
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            stat = entry.stat()
            out.append(
                {
                    "kind": k,
                    "name": entry.name,
                    "path": str(entry),
                    "size_bytes": _dir_size(entry),
                    "mtime": stat.st_mtime,
                    "last_used": datetime.fromtimestamp(stat.st_mtime, UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
            )
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out


def delete_profile(kind: str, name: str) -> Path:
    target = profile_dir(kind, name)
    if not target.exists():
        raise FileNotFoundError(f"no profile at {target}")
    shutil.rmtree(target)
    return target
