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
from octowright._paths import atomic_write_text, reject_unsafe_path

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
    # slug() preserves "." and "-" so a literal "../etc/passwd" slugs to
    # itself; the containment check is the security boundary, not slug().
    path = reject_unsafe_path(
        GOLDENS_DIR / f"{_slug(name)}.json",
        GOLDENS_DIR,
        label=f"golden name {name!r}",
    )

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
    # Atomic temp-sibling + os.replace: a same-user attacker who swaps the
    # destination for a symlink in the resolve()->write() window gets the
    # symlink replaced, not followed (see atomic_write_text).
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def load_golden(name: str) -> dict[str, Any]:
    """Return full golden dict. Raises FileNotFoundError if missing."""
    path = reject_unsafe_path(
        GOLDENS_DIR / f"{_slug(name)}.json",
        GOLDENS_DIR,
        label=f"golden name {name!r}",
    )
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
    path = reject_unsafe_path(
        GOLDENS_DIR / f"{_slug(name)}.json",
        GOLDENS_DIR,
        label=f"golden name {name!r}",
    )
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


def _identity_key(node: Any, index: int) -> tuple[Any, ...]:
    # Positional matching produces O(n) spurious diffs when a node is
    # inserted at the head of a sibling list. Keying by semantic identity
    # localizes the diff to the actually-added/removed node. Unkeyed
    # values fall back to ("__index__", index) for legacy positional behavior.
    if not isinstance(node, dict):
        return ("__index__", index)
    role = node.get("role")
    if role is None:
        return ("__index__", index)
    return _identity_key_for_role(node, role, index)


def _identity_key_for_role(node: dict, role: Any, index: int) -> tuple[Any, ...]:
    name = node.get("name")
    if name is not None:
        return ("role+name", role, name)
    ref_id = node.get("ref", node.get("id"))
    if ref_id is not None:
        return ("role+ref", role, ref_id)
    label = node.get("accessible_label") or node.get("label") or node.get("text")
    if label is not None:
        return ("role+label", role, label)
    return ("role+index", role, index)


def _diff_unkeyed_lists(exp_list: list[Any], act_list: list[Any], path: str, diffs: list[dict[str, Any]]) -> None:
    for i in range(max(len(exp_list), len(act_list))):
        child_path = f"{path}/{i}"
        if i >= len(exp_list):
            diffs.append({"path": child_path, "op": "added", "expected": None, "actual": act_list[i]})
        elif i >= len(act_list):
            diffs.append({"path": child_path, "op": "removed", "expected": exp_list[i], "actual": None})
        else:
            _diff_nodes(exp_list[i], act_list[i], child_path, diffs)


def _first_index_by_key(keys: list[tuple[Any, ...]]) -> dict[tuple[Any, ...], int]:
    out: dict[tuple[Any, ...], int] = {}
    for i, k in enumerate(keys):
        out.setdefault(k, i)
    return out


def _diff_matched_pairs(
    exp_list: list[Any],
    act_list: list[Any],
    exp_keys: list[tuple[Any, ...]],
    act_idx_by_key: dict[tuple[Any, ...], int],
    path: str,
    diffs: list[dict[str, Any]],
) -> set[int]:
    matched_act: set[int] = set()
    for i, key in enumerate(exp_keys):
        child_path = f"{path}/{i}"
        j = act_idx_by_key.get(key)
        if j is None:
            diffs.append({"path": child_path, "op": "removed", "expected": exp_list[i], "actual": None})
        else:
            matched_act.add(j)
            _diff_nodes(exp_list[i], act_list[j], child_path, diffs)
    return matched_act


def _diff_child_lists(
    exp_list: list[Any],
    act_list: list[Any],
    path: str,
    diffs: list[dict[str, Any]],
) -> None:
    exp_keys = [_identity_key(n, i) for i, n in enumerate(exp_list)]
    act_keys = [_identity_key(n, i) for i, n in enumerate(act_list)]

    # If either side is fully unkeyed, preserve legacy positional behavior
    # so callers passing lists of scalars or unkeyed dicts see stable diffs.
    if all(k[0] == "__index__" for k in exp_keys) or all(k[0] == "__index__" for k in act_keys):
        _diff_unkeyed_lists(exp_list, act_list, path, diffs)
        return

    act_idx_by_key = _first_index_by_key(act_keys)
    exp_idx_by_key = _first_index_by_key(exp_keys)
    matched_act = _diff_matched_pairs(exp_list, act_list, exp_keys, act_idx_by_key, path, diffs)

    for j, key in enumerate(act_keys):
        # Skip matched indices and keys already accounted for on the expected
        # side (avoids double-reporting a duplicate-key collision).
        if j in matched_act or key in exp_idx_by_key:
            continue
        diffs.append({"path": f"{path}/{j}", "op": "added", "expected": None, "actual": act_list[j]})


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
        _diff_child_lists(exp, act, path, diffs)
    else:
        if exp != act:
            diffs.append({"path": path, "op": "changed", "expected": exp, "actual": act})
