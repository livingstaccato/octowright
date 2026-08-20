# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright import defaults
from octowright._paths import atomic_write_text, reject_unsafe_path
from octowright.macros.recording_import import iter_macro_actions
from octowright.macros.substitution import normalise_parameters, substitute_in_action
from octowright.mcp_types import MacroListEntry
from octowright.private_paths import secure_artifact_tree

log = get_logger(__name__)

SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
# MACROS_DIR lives in defaults.py. Re-exported so tests that reload this
# module (after setenv'ing OCTOWRIGHT_MACROS_DIR + reloading defaults) see
# a fresh value here too.
MACROS_DIR: Path = defaults.MACROS_DIR


def slug(name: str) -> str:
    cleaned = SLUG_RE.sub("-", name.strip())
    cleaned = cleaned.strip("-.")
    if not cleaned:
        raise ValueError(f"macro name {name!r} produced an empty slug")
    return cleaned


def macro_path(name: str) -> Path:
    # slug() preserves "." and "-", so a literal "../etc/passwd" slugs to
    # itself and would escape MACROS_DIR. The containment check below is
    # the security boundary — keep it even if slug() is hardened later.
    candidate = MACROS_DIR / f"{slug(name)}.json"
    # MACROS_DIR may not yet exist; resolve relative to its absolute form
    # so the comparison still works on a fresh install.
    return reject_unsafe_path(candidate, MACROS_DIR, label=f"macro name {name!r}")


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def save_macro(
    *,
    recording_path: Path,
    name: str,
    description: str | None = None,
    parameters: list[str] | dict[str, str] | None = None,
    include_launch: bool = False,
) -> Path:
    param_map = normalise_parameters(parameters)
    value_to_name = {value: key for key, value in param_map.items()}
    actions = [
        substitute_in_action(entry, value_to_name)
        for entry in iter_macro_actions(recording_path, include_launch=include_launch, strict_json=True)
    ]

    dest = macro_path(name)
    created_at = now_iso()
    if dest.exists():
        try:
            existing = json.loads(dest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = None
        if existing is not None:
            # slug() collapses distinct names onto the same file (e.g.
            # "report sync" and "report-sync" → report-sync.json). Re-saving
            # under the SAME display name is an update; a DIFFERENT name would
            # silently clobber an unrelated macro, so reject it.
            existing_name = existing.get("name")
            if existing_name is not None and existing_name != name:
                raise ValueError(
                    f"macro name {name!r} collides with existing macro {existing_name!r} "
                    f"(both map to {dest.name}); choose a distinct name or delete the existing "
                    f"macro first with `macro_delete name={existing_name!r}`"
                )
            created_at = existing.get("created_at", created_at)

    now = now_iso()
    macro: dict[str, Any] = {
        "name": name,
        "description": description,
        "parameters": list(param_map.keys()),
        "created_at": created_at,
        "updated_at": now,
        "actions": actions,
    }

    MACROS_DIR.mkdir(parents=True, exist_ok=True)
    secure_artifact_tree(MACROS_DIR, MACROS_DIR)
    atomic_write_text(dest, json.dumps(macro, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("octowright.macro.saved", name=name, path=str(dest), action_count=len(actions))
    return dest


def list_macros() -> list[MacroListEntry]:
    if not MACROS_DIR.exists():
        return []
    out: list[MacroListEntry] = []
    for path in MACROS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "name": data.get("name", path.stem),
                "description": data.get("description"),
                "parameters": data.get("parameters", []),
                "path": str(path),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "action_count": len(data.get("actions", [])),
            }
        )
    out.sort(key=lambda macro: macro.get("updated_at") or "", reverse=True)
    return out


def load_macro(name: str) -> dict[str, Any]:
    path = macro_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"no macro named {name!r} at {path}; list saved macros with `macro_list` or "
            f"record one with `macro_save instance_id=<id> name={name!r}`"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_macro(*, name: str, macro: dict[str, Any]) -> Path:
    now = now_iso()
    to_write = copy.deepcopy(macro)
    to_write["name"] = name
    to_write.setdefault("created_at", now)
    to_write["updated_at"] = now
    dest = macro_path(name)
    if dest.exists():
        try:
            existing = json.loads(dest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = None
        if existing is not None:
            # Same collision guard as save_macro: slug() collapses distinct
            # display names onto the same file (e.g. "nightly backup" and
            # "nightly!backup" both -> nightly-backup.json). Re-writing under
            # the SAME display name is an update; a DIFFERENT name would
            # silently clobber an unrelated macro, so reject it.
            existing_name = existing.get("name")
            if existing_name is not None and existing_name != name:
                raise ValueError(
                    f"macro name {name!r} collides with existing macro {existing_name!r} "
                    f"(both map to {dest.name}); choose a distinct name or delete the existing "
                    f"macro first with `macro_delete name={existing_name!r}`"
                )
    dest.parent.mkdir(parents=True, exist_ok=True)
    secure_artifact_tree(dest.parent, MACROS_DIR)
    atomic_write_text(dest, json.dumps(to_write, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("octowright.macro.written", name=name, path=str(dest), action_count=len(to_write.get("actions", [])))
    return dest


def delete_macro(name: str) -> Path:
    path = macro_path(name)
    if not path.exists():
        raise FileNotFoundError(f"no macro named {name!r} at {path}; list saved macros with `macro_list`")
    path.unlink()
    log.info("octowright.macro.deleted", name=name, path=str(path))
    return path
