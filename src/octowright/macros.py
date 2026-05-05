# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import copy
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from .defaults import PROFILES_DIR
from .macros_runtime import dispatch_one as _runtime_dispatch_one
from .macros_runtime import dispatch_simple as _runtime_dispatch_simple
from .server.macro_semantic import summarize_action

if TYPE_CHECKING:
    from .session import BrowserSession

log = get_logger(__name__)

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

_SEMANTIC_LOCATOR_KEYS = ("role", "role_name", "label", "text", "test_id", "role_exact")
_NON_ARIA_NOISE_KEYS = ("role", "role_name", "test_id", "role_exact")
_RECORDING_NOISE_KEYS = ("action", "ts", "kind", "profile", "instance_id")

# MACROS_DIR sits next to profiles/ by default; env var overrides.
MACROS_DIR: Path = Path(os.environ.get("OCTOWRIGHT_MACROS_DIR", str(PROFILES_DIR.parent / "macros")))

# Actions that are lifecycle/inspection-only and are stripped from macros by default.
_ALWAYS_STRIP = {"close", "snapshot"}
_LIFECYCLE = {"launch"}


def _slug(name: str) -> str:
    cleaned = _SLUG_RE.sub("-", name.strip())
    cleaned = cleaned.strip("-.")
    if not cleaned:
        raise ValueError(f"macro name {name!r} produced an empty slug")
    return cleaned


def _macro_path(name: str) -> Path:
    return MACROS_DIR / f"{_slug(name)}.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalise_parameters(
    parameters: list[str] | dict[str, str] | None,
) -> dict[str, str]:
    """Return {param_name: value_to_match} regardless of input form."""
    if parameters is None:
        return {}
    if isinstance(parameters, dict):
        return parameters
    # positional list — auto-name
    return {f"params[{i}]": v for i, v in enumerate(parameters)}


def _substitute_in_action(
    action: dict[str, Any],
    value_to_name: dict[str, str],
) -> dict[str, Any]:
    """Walk an action dict; replace string values that exactly equal a parameter
    value with the {{name}} placeholder.  Returns a shallow-copied dict."""
    result: dict[str, Any] = {}
    for k, v in action.items():
        if isinstance(v, str) and v in value_to_name:
            result[k] = "{{" + value_to_name[v] + "}}"
        else:
            result[k] = v
    return result


def _action_kwargs(action: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``action`` without session-tracking/meta keys."""
    return {k: v for k, v in action.items() if k not in _RECORDING_NOISE_KEYS}


def _strip_non_aria_noise(kind: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop semantic metadata for methods that don't understand it."""
    if kind in {"click", "fill", "click_by", "fill_by"}:
        return dict(kwargs)

    cleaned = dict(kwargs)
    for k in _NON_ARIA_NOISE_KEYS:
        cleaned.pop(k, None)
    return cleaned


def load_macro_from_recording(
    path: Path,
    include_launch: bool = False,
) -> list[dict[str, Any]]:
    """Read a JSONL recording and return its list of actions (stripped/filtered)."""
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    actions: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        action_type = entry.get("action", "")
        if action_type in _ALWAYS_STRIP:
            continue
        if action_type in _LIFECYCLE and not include_launch:
            continue
        actions.append(entry)
    return actions


def save_macro(
    *,
    recording_path: Path,
    name: str,
    description: str | None = None,
    parameters: list[str] | dict[str, str] | None = None,
    include_launch: bool = False,
) -> Path:
    """Read a JSONL recording and save it as a named, parameterised macro.

    Strips ``close`` and ``snapshot`` entries unconditionally; strips ``launch``
    unless *include_launch* is ``True``.  String fields whose value exactly
    matches a supplied parameter value are replaced with ``{{name}}``
    placeholders.

    Returns the path of the written JSON file.
    """
    param_map = _normalise_parameters(parameters)
    # Invert: value → name (for substitution during save)
    value_to_name = {v: k for k, v in param_map.items()}

    raw_lines = recording_path.read_text(encoding="utf-8").splitlines()
    actions: list[dict[str, Any]] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        action_type = entry.get("action", "")
        if action_type in _ALWAYS_STRIP:
            continue
        if action_type in _LIFECYCLE and not include_launch:
            continue
        processed = _substitute_in_action(entry, value_to_name)
        actions.append(processed)

    dest = _macro_path(name)

    # Preserve created_at if the macro already exists.
    created_at = _now_iso()
    if dest.exists():
        try:
            existing = json.loads(dest.read_text(encoding="utf-8"))
            created_at = existing.get("created_at", created_at)
        except (json.JSONDecodeError, OSError):
            pass

    now = _now_iso()
    macro: dict[str, Any] = {
        "name": name,
        "description": description,
        "parameters": list(param_map.keys()),
        "created_at": created_at,
        "updated_at": now,
        "actions": actions,
    }

    MACROS_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(macro, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("octowright.macro.saved", name=name, path=str(dest), action_count=len(actions))
    return dest


def list_macros() -> list[dict[str, Any]]:
    """Return macro summaries sorted by ``updated_at`` descending."""
    if not MACROS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in MACROS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "name": data.get("name", p.stem),
                "description": data.get("description"),
                "parameters": data.get("parameters", []),
                "path": str(p),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "action_count": len(data.get("actions", [])),
            }
        )
    out.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
    return out


def load_macro(name: str) -> dict[str, Any]:
    """Load a macro by name.  Raises ``FileNotFoundError`` if not found."""
    p = _macro_path(name)
    if not p.exists():
        raise FileNotFoundError(
            f"no macro named {name!r} at {p}; list saved macros with `macro_list` or "
            f"record one with `macro_save instance_id=<id> name={name!r}`"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def delete_macro(name: str) -> Path:
    """Delete a macro file.  Raises ``FileNotFoundError`` if not found."""
    p = _macro_path(name)
    if not p.exists():
        raise FileNotFoundError(f"no macro named {name!r} at {p}; list saved macros with `macro_list`")
    p.unlink()
    log.info("octowright.macro.deleted", name=name, path=str(p))
    return p


def _substitute_value(v: Any, args: dict[str, Any]) -> Any:
    """Recursively replace ``{{name}}`` placeholders in string values."""
    if isinstance(v, str):
        # Replace all {{name}} occurrences in the string.
        def replacer(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in args:
                raise KeyError(f"placeholder {{{{{key}}}}} has no matching arg; available: {list(args)}")
            return str(args[key])

        return re.sub(r"\{\{([^}]+)\}\}", replacer, v)
    if isinstance(v, dict):
        return {k: _substitute_value(val, args) for k, val in v.items()}
    if isinstance(v, list):
        return [_substitute_value(item, args) for item in v]
    return v


def substitute(actions: list[dict[str, Any]], args: dict[str, Any]) -> list[dict[str, Any]]:
    """Replace ``{{name}}`` placeholders in every string field of each action.

    Raises ``KeyError`` with the missing placeholder name if any placeholder
    has no matching key in *args*.  Extra keys in *args* are silently ignored.
    """
    return [_substitute_value(copy.deepcopy(action), args) for action in actions]


async def _dispatch_one(session: BrowserSession, action: dict[str, Any]) -> tuple[int, int]:
    return await _runtime_dispatch_one(
        session,
        action,
        semantic_keys=_SEMANTIC_LOCATOR_KEYS,
        strip_non_aria_noise=_strip_non_aria_noise,
        action_kwargs=_action_kwargs,
    )


async def _dispatch_simple(session: BrowserSession, action: dict[str, Any]) -> tuple[int, int]:
    return await _runtime_dispatch_simple(
        session,
        action,
        semantic_keys=_SEMANTIC_LOCATOR_KEYS,
        strip_non_aria_noise=_strip_non_aria_noise,
        action_kwargs=_action_kwargs,
    )


async def _suggest_fix(session: BrowserSession, action: dict[str, Any]) -> str | None:
    """Attempt to find a new selector or locator if an action fails.
    Returns a suggestion string (e.g. "try click_by(text='Log in') instead") or None.
    """
    selector = action.get("selector")
    if not selector:
        return None

    # Get A11y tree
    try:
        snapshot = await session.snapshot()
        aria = snapshot["aria"]
    except Exception:
        return None

    summary = summarize_action(action)
    prompt = (
        f"I was trying to {summary}, but '{selector}' failed.\n\n"
        f"Current A11y tree:\n---\n{aria}\n---\n\n"
        "Based on the A11y tree, what should I use instead?"
    )

    return prompt


def _semantic_replacement(action: dict[str, Any]) -> dict[str, Any] | None:
    kind = action.get("action")
    if kind not in {"click", "fill"} or not action.get("selector"):
        return None

    semantic = {k: action[k] for k in _SEMANTIC_LOCATOR_KEYS if k in action and action[k] is not None}
    if not semantic:
        return None

    replacement: dict[str, Any] = {"action": f"{kind}_by", **semantic}
    if kind == "fill" and "value" in action:
        replacement["value"] = action["value"]
    if "timeout_ms" in action:
        replacement["timeout_ms"] = action["timeout_ms"]
    return replacement


def _replacement_preview(action: dict[str, Any]) -> str:
    kind = action.get("action")
    if kind == "click_by":
        target = action.get("role_name") or action.get("label") or action.get("text") or action.get("test_id")
        return f"Click by {target!r}" if target else "Click by semantic locator"
    if kind == "fill_by":
        target = action.get("role_name") or action.get("label") or action.get("text") or action.get("test_id")
        value = action.get("value", "")
        return f"Fill by {target!r} with {value!r}" if target else f"Fill by semantic locator with {value!r}"
    return summarize_action(action)


def repair_preview(name: str) -> dict[str, Any]:
    """Return non-mutating repair suggestions for selector-based macro actions."""
    macro = load_macro(name)
    macro_name = macro.get("name") or name
    suggestions: list[dict[str, Any]] = []
    for idx, action in enumerate(macro.get("actions", [])):
        if not isinstance(action, dict) or "selector" not in action:
            continue

        replacement = _semantic_replacement(action)
        selector = action.get("selector")
        prompt = (
            f"Review selector {selector!r} for action {idx}. "
            "If it no longer matches, compare the stored semantic fields against the current page."
        )
        suggestions.append(
            {
                "macro": macro_name,
                "action_index": idx,
                "original_action": copy.deepcopy(action),
                "source": "stored_heuristic",
                "replacement_action": replacement,
                "action_preview": _replacement_preview(replacement) if replacement else None,
                "prompt": prompt,
            }
        )

    return {"macro": macro_name, "suggestions": suggestions}


async def run_macro(
    session: BrowserSession,
    name: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load *name*, substitute *args*, and execute each supported action.

    Returns ``{"macro": name, "executed": N, "skipped": M, "args_used": {...}}``.
    Conditional action types (if_selector / try / try_each) are recognised — see
    `octowright.conditional` for their JSON shapes.
    """
    macro = load_macro(name)
    effective_args = args or {}
    actions = substitute(macro.get("actions", []), effective_args)

    executed = 0
    skipped = 0

    for i, action in enumerate(actions):
        try:
            e, s = await _dispatch_one(session, action)
        except Exception as exc:
            bundle = await session.diagnostic_bundle()
            # HEALING: try to find a suggestion
            fix_suggestion = await _suggest_fix(session, action)

            payload: dict[str, Any] = {
                "macro": name,
                "failed_at_step": i,
                "failed_action": action,
                "original": repr(exc),
                "bundle": bundle,
            }
            if fix_suggestion:
                payload["healing_suggestion"] = fix_suggestion

            raise RuntimeError(payload) from exc
        executed += e
        skipped += s

    log.info(
        "octowright.macro.run",
        name=name,
        executed=executed,
        skipped=skipped,
    )
    return {"macro": name, "executed": executed, "skipped": skipped, "args_used": effective_args}


# ---------------------------------------------------------------------------
# Sequence runner
# ---------------------------------------------------------------------------


async def run_sequence(
    *,
    session: Any,
    names: list[str],
    args_list: list[dict[str, Any]] | None = None,
    stop_on_failure: bool = True,
) -> dict[str, Any]:
    """Run several macros back-to-back against one session.

    *args_list[i]* supplies args for *names[i]*; omitted entries use ``{}``.
    If *stop_on_failure* is ``True`` (default), the first exception aborts
    the chain and the raised error is re-raised so callers see the original
    traceback. If ``False``, each macro's outcome is collected and the chain
    keeps going.

    Returns::

        {
            "sequence": [name1, name2, ...],
            "steps": [
               {"macro": name1, "ok": True,  "executed": N, "skipped": M, "args_used": {...}},
               {"macro": name2, "ok": False, "error": "...", "args_used": {...}},
               ...
            ],
            "ok": False,   # True iff every step was ok
        }
    """
    resolved_args: list[dict[str, Any]] = []
    for i in range(len(names)):
        if args_list is not None and i < len(args_list):
            resolved_args.append(args_list[i] or {})
        else:
            resolved_args.append({})

    steps: list[dict[str, Any]] = []
    all_ok = True

    for name, step_args in zip(names, resolved_args, strict=True):
        try:
            outcome = await run_macro(session=session, name=name, args=step_args)
            steps.append({**outcome, "ok": True})
        except Exception as exc:
            all_ok = False
            steps.append({"macro": name, "ok": False, "error": str(exc), "args_used": step_args})
            if stop_on_failure:
                raise
            # stop_on_failure=False: collect and continue

    return {"sequence": names, "steps": steps, "ok": all_ok}
