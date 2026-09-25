# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Helpers shared between the Python (``export.py``) and TypeScript
(``export_ts.py``) replay-script emitters. Split out so neither emitter
module has to import the other -- both import from here instead."""

from __future__ import annotations

from typing import Any

from octowright.defaults import SUPPORTED_KINDS

_SEMANTIC_LOCATOR_KEYS = ("role", "label", "text", "test_id")

_DIALOG_POLICIES = ("accept", "dismiss", "manual")


def _has_semantic_locator(entry: dict) -> bool:
    return any(entry.get(key) for key in _SEMANTIC_LOCATOR_KEYS)


def _launch_viewport(entry: dict) -> dict[str, int]:
    vp = entry.get("viewport")
    if isinstance(vp, dict) and isinstance(vp.get("w"), int) and isinstance(vp.get("h"), int):
        return {"w": vp["w"], "h": vp["h"]}
    return {"w": 1280, "h": 800}


def _safe_int(value: Any, *, action: str, field: str, default: int = 0) -> int:
    """Coerce a JSONL-recorded field to a genuine ``int`` for safe splicing into generated source.

    A recording or saved macro may be attacker-controlled (see ``ssrf.py``'s
    threat model, and ``browser_pool/options.py`` excluding ``executable_path``/
    ``launch_args`` from replay for the identical reason). These numeric fields
    are interpolated directly into generated Python/TypeScript source as a bare
    number; without coercion, a crafted non-numeric string becomes executable
    code the instant the exported script runs. A missing/``None`` value uses
    ``default``; a present-but-non-numeric value fails LOUDLY at export time
    instead of silently embedding attacker text as source.
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"export: action {action!r} field {field!r} must be a number, got {value!r}") from exc


def _validate_kind(kind: object, *, action: str = "launch") -> str:
    """Validate a recorded browser ``kind`` against the fixed engine allowlist.

    ``kind`` is used in generated code in *identifier* position (an attribute
    lookup / object-index), where repr()/json.dumps() quoting alone would only
    turn an injection into a SyntaxError, not make it safe. The allowlist check
    here plus the getattr/index-based lookup emitted by the callers keeps a
    validation bug from reaching code execution.
    """
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"export: action {action!r} has unsupported kind {kind!r}; must be one of {SUPPORTED_KINDS}")
    return str(kind)


def _validate_dialog_policy(policy: object, *, action: str = "set_dialog_policy") -> str:
    if policy not in _DIALOG_POLICIES:
        raise ValueError(
            f"export: action {action!r} has unsupported policy {policy!r}; must be one of {_DIALOG_POLICIES}"
        )
    return str(policy)


def _route_pattern(entry: dict) -> str:
    """The URL pattern of a ``mock_route``/``unmock_route`` row.

    The recorder writes ``pattern``; ``url_pattern`` is the session method's
    parameter name, which a hand-written macro row may carry instead. A row
    carrying both, disagreeing, is refused rather than resolved: which one wins
    is exactly what a crafted row would choose to exploit.
    """
    pattern, url_pattern = entry.get("pattern"), entry.get("url_pattern")
    if pattern is not None and url_pattern is not None and pattern != url_pattern:
        raise ValueError(f"export: action {entry.get('action')!r} has conflicting pattern and url_pattern")
    chosen = pattern if pattern is not None else url_pattern
    if chosen is None:
        raise ValueError(f"export: action {entry.get('action')!r} needs a pattern")
    return str(chosen)


def _input_file_paths(entry: dict) -> list:
    """The files of a ``set_input_files`` row.

    The recorder has always written ``paths``. ``files`` is what the emitters
    used to read -- no recorder wrote it, but a hand-built row may carry it.
    """
    paths = entry.get("paths")
    return list(paths if paths is not None else entry.get("files") or [])


#: What an exported script says at a step it cannot run, instead of dropping it.
_UNSUPPORTED = (
    "{kind} is not supported by browser_export_script: it needs state a linear script "
    "does not keep. Export with macro_export_cli, which runs it"
)
