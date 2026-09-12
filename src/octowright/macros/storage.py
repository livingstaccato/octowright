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
from octowright.macros.privacy import is_credential_key
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


def _value_to_name(param_map: dict[str, str]) -> dict[str, str]:
    """Invert the parameter map, refusing two parameters that share a value.

    Substitution is keyed by recorded value, so a shared value cannot say which
    field belongs to which parameter: ``{"username": "admin", "password":
    "admin"}`` used to turn BOTH fields into ``{{password}}`` and silently drop
    ``{{username}}``. The message names the parameters and never the value,
    which is often a credential and would otherwise reach the MCP transcript.
    """
    names_by_value: dict[str, list[str]] = {}
    for name, value in param_map.items():
        names_by_value.setdefault(value, []).append(name)
    shared = sorted(sorted(names) for names in names_by_value.values() if len(names) > 1)
    if shared:
        groups = "; ".join(", ".join(repr(n) for n in names) for names in shared)
        raise ValueError(
            f"parameters {groups} share the same value, so save_macro cannot tell which recorded "
            "field belongs to which parameter; record them with distinct values"
        )
    return {value: names[0] for value, names in names_by_value.items()}


def _redacted_fields(actions: list[dict[str, Any]]) -> list[tuple[int, str]]:
    marker = defaults.REDACTED_INPUT_PLACEHOLDER
    return [(i, key) for i, action in enumerate(actions) for key, value in action.items() if value == marker]


def _field_label(action: dict[str, Any], index: int) -> str:
    selector = action.get("selector")
    return str(selector) if selector else f"action {index} ({action.get('action', '?')})"


def _unmatched_credential_parameters(entries: list[dict[str, Any]], param_map: dict[str, str]) -> list[str]:
    recorded = {value for entry in entries for value in entry.values() if isinstance(value, str)}
    return sorted(name for name, value in param_map.items() if value not in recorded and is_credential_key(name))


def _bind_redacted_inputs(
    actions: list[dict[str, Any]], entries: list[dict[str, Any]], param_map: dict[str, str]
) -> list[dict[str, Any]]:
    """Fill a field redacted at record time, or refuse to save a macro that cannot replay.

    ``OCTOWRIGHT_REDACT_INPUTS`` (default ``passwords``) records a password field as
    ``REDACTED_INPUT_PLACEHOLDER``, so the declared value is never there to match
    and the marker used to be written into the macro -- which replay then typed
    into the page. A redacted field is bound only when exactly one field was
    redacted and exactly one credential-named parameter matched nothing else in
    the recording; every other case is ambiguous and refused, the way replay
    already refuses a redacted header rather than failing confusingly later.
    """
    redacted = _redacted_fields(actions)
    if not redacted:
        return actions
    candidates = _unmatched_credential_parameters(entries, param_map)
    if len(redacted) == 1 and len(candidates) == 1:
        index, key = redacted[0]
        actions[index] = {**actions[index], key: "{{" + candidates[0] + "}}"}
        return actions
    fields = ", ".join(_field_label(actions[i], i) for i, _key in redacted)
    raise ValueError(_redaction_refusal(fields, len(redacted), candidates))


def _redaction_refusal(fields: str, field_count: int, candidates: list[str]) -> str:
    lead = (
        f"the recording holds {field_count} input field(s) redacted at record time ({fields}), because "
        "OCTOWRIGHT_REDACT_INPUTS hid the typed value; saving would write the redaction marker into the "
        "macro and replay would type it into the page. "
    )
    if not candidates:
        return lead + (
            "Declare a credential-named parameter (for example `password`) for that field, or re-record "
            "with OCTOWRIGHT_REDACT_INPUTS=off in a trusted environment."
        )
    if field_count > 1:
        return lead + (
            "Only a single redacted field can be bound automatically; re-record the fields separately, "
            "or with OCTOWRIGHT_REDACT_INPUTS=off in a trusted environment."
        )
    names = ", ".join(repr(n) for n in candidates)
    return lead + f"Parameters {names} could each fill it; declare only the one that was typed there."


def save_macro(
    *,
    recording_path: Path,
    name: str,
    description: str | None = None,
    parameters: list[str] | dict[str, str] | None = None,
    include_launch: bool = False,
) -> Path:
    param_map = normalise_parameters(parameters)
    value_to_name = _value_to_name(param_map)
    entries = list(iter_macro_actions(recording_path, include_launch=include_launch, strict_json=True))
    actions = _bind_redacted_inputs(
        [substitute_in_action(entry, value_to_name) for entry in entries], entries, param_map
    )

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
