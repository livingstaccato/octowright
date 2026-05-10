# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octowright import defaults

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

# GOLDENS_DIR lives in defaults.py. Re-exported so tests that reload this
# module (after setenv'ing OCTOWRIGHT_GOLDENS_DIR + reloading defaults) see
# a fresh value here too.
GOLDENS_DIR: Path = defaults.GOLDENS_DIR


def _slug(name: str) -> str:
    cleaned = _SLUG_RE.sub("-", name.strip())
    cleaned = cleaned.strip("-.")
    if not cleaned:
        raise ValueError(f"golden name {name!r} produced an empty slug")
    return cleaned


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def save_golden(
    *,
    name: str,
    tree: dict[str, Any],
    url: str | None = None,
    description: str | None = None,
) -> Path:
    """Write / overwrite <slug(name)>.json. Preserves created_at on overwrite."""
    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    path = GOLDENS_DIR / f"{_slug(name)}.json"

    created_at = _now()
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            created_at = existing.get("created_at", created_at)
        except Exception:
            pass

    payload: dict[str, Any] = {
        "name": name,
        "description": description,
        "url": url,
        "created_at": created_at,
        "updated_at": _now(),
        "tree": tree,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_golden(name: str) -> dict[str, Any]:
    """Return full golden dict. Raises FileNotFoundError if missing."""
    path = GOLDENS_DIR / f"{_slug(name)}.json"
    if not path.exists():
        raise FileNotFoundError(f"no golden named {name!r} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_goldens() -> list[dict[str, Any]]:
    """Return [{name, description, path, created_at, updated_at, url}, ...] sorted by updated_at desc."""
    if not GOLDENS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for entry in GOLDENS_DIR.glob("*.json"):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append(
            {
                "name": data.get("name", entry.stem),
                "description": data.get("description"),
                "path": str(entry),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "url": data.get("url"),
            }
        )
    out.sort(key=lambda g: g.get("updated_at") or "", reverse=True)
    return out


def delete_golden(name: str) -> Path:
    """Delete a golden by name. Raises FileNotFoundError if missing."""
    path = GOLDENS_DIR / f"{_slug(name)}.json"
    if not path.exists():
        raise FileNotFoundError(f"no golden named {name!r} at {path}")
    path.unlink()
    return path


# ---------------------------------------------------------------------------
# Tree diffing
# ---------------------------------------------------------------------------


def diff_trees(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a list of differences between two accessibility trees.

    Each entry: {"path": "root/children/0/name", "op": "changed"|"added"|"removed",
    "expected": ..., "actual": ...}

    Recursively walks both trees; reports leaf-value mismatches, added/removed
    keys, and length mismatches in arrays.
    """
    diffs: list[dict[str, Any]] = []
    _diff_nodes(expected, actual, "root", diffs)
    return diffs


def _diff_nodes(
    exp: Any,
    act: Any,
    path: str,
    diffs: list[dict[str, Any]],
) -> None:
    if isinstance(exp, dict) and isinstance(act, dict):
        all_keys = set(exp) | set(act)
        for key in sorted(all_keys):
            child_path = f"{path}/{key}"
            if key not in exp:
                diffs.append({"path": child_path, "op": "added", "expected": None, "actual": act[key]})
            elif key not in act:
                diffs.append({"path": child_path, "op": "removed", "expected": exp[key], "actual": None})
            else:
                _diff_nodes(exp[key], act[key], child_path, diffs)
    elif isinstance(exp, list) and isinstance(act, list):
        max_len = max(len(exp), len(act))
        for i in range(max_len):
            child_path = f"{path}/{i}"
            if i >= len(exp):
                diffs.append({"path": child_path, "op": "added", "expected": None, "actual": act[i]})
            elif i >= len(act):
                diffs.append({"path": child_path, "op": "removed", "expected": exp[i], "actual": None})
            else:
                _diff_nodes(exp[i], act[i], child_path, diffs)
    else:
        if exp != act:
            diffs.append({"path": path, "op": "changed", "expected": exp, "actual": act})
