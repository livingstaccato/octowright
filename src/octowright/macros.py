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

from .defaults import DEFAULT_ACTION_TIMEOUT_MS, PROFILES_DIR
from .server.macro_semantic import summarize_action

if TYPE_CHECKING:
    from .session import BrowserSession

log = get_logger(__name__)

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

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


# Actions that should never be re-executed during replay.
_REPLAY_SKIP = {"launch", "close", "snapshot"}


async def _dispatch_simple(session: BrowserSession, action: dict[str, Any]) -> tuple[int, int]:
    """Run one non-conditional action. Returns (executed, skipped). Raises on action failure.

    `executed` and `skipped` are 0/1 — never both. Conditional actions (if_selector,
    try, try_each) are dispatched in `_dispatch_one`, not here.
    """
    kind = action.get("action", "")
    if kind in _REPLAY_SKIP:
        return 0, 1
    if kind == "navigate":
        await session.navigate(action["url"])
    elif kind == "click":
        await session.click(action["selector"])
    elif kind == "type":
        await session.type_text(action["selector"], action.get("text", ""), action.get("delay_ms"))
    elif kind == "fill":
        await session.fill(action["selector"], action.get("value", ""))
    elif kind == "press_key":
        await session.press_key(action["key"])
    elif kind == "screenshot":
        path_str = action.get("path")
        if not path_str:
            return 0, 1
        await session.screenshot(Path(path_str))
    elif kind == "evaluate":
        await session.evaluate(action["expression"])
    elif kind == "wait_for":
        await session.wait_for(action.get("selector"), action.get("text"), action.get("timeout_ms"))
    elif kind == "expect_url":
        await _check_url(session.page, action["pattern"], action.get("mode", "regex"))
    elif kind == "expect_text":
        await _check_text(
            session.page,
            action["selector"],
            action["text"],
            action.get("mode", "contains"),
            action.get("timeout_ms"),
        )
    elif kind == "expect_selector":
        await _check_selector(session.page, action["selector"], action.get("present", True), action.get("timeout_ms"))
    elif kind == "expect_js":
        await _check_js(session.page, action["expression"], action.get("equals"))
    elif kind == "mock_route":
        await session.mock_route(
            action["pattern"],
            status=action.get("status", 200),
            body=action.get("body"),
            content_type=action.get("content_type", "application/json"),
            headers=action.get("headers"),
        )
    elif kind == "unmock_route":
        await session.unmock_route(action["pattern"])
    elif kind == "set_dialog_policy":
        session.set_dialog_policy(action["policy"], action.get("prompt_text"))
    elif kind == "set_input_files":
        await session.set_input_files(action["selector"], action.get("paths", []))
    else:
        return 0, 1
    return 1, 0


async def _dispatch_one(session: BrowserSession, action: dict[str, Any]) -> tuple[int, int]:
    """Run one action of any type. Returns (executed, skipped). Raises on failure.

    Conditional actions (if_selector / try / try_each) recursively call back here
    for their child actions, so arbitrary nesting works.
    """
    from . import conditional as _cond

    if action.get("action") in _cond.CONDITIONAL_ACTIONS:
        return await _cond.dispatch_conditional(session, action, _dispatch_one)
    return await _dispatch_simple(session, action)


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
# Assertion helpers — small coroutines used by both MCP tools and run_macro.
# ---------------------------------------------------------------------------


async def _check_url(page: Any, pattern: str, mode: str) -> str:
    """Check the page URL against *pattern*. Returns the actual URL on success.

    Raises ``RuntimeError`` with a descriptive message on mismatch.
    *mode* must be ``'regex'``, ``'equals'``, or ``'contains'``.
    """
    actual: str = page.url
    if mode == "equals":
        if actual != pattern:
            raise RuntimeError(f'URL mismatch: expected "{pattern}" (equals), got "{actual}"')
    elif mode == "contains":
        if pattern not in actual:
            raise RuntimeError(f'URL mismatch: expected substring "{pattern}" (contains), got "{actual}"')
    elif mode == "regex":
        if not re.search(pattern, actual):
            raise RuntimeError(f'URL mismatch: expected pattern "{pattern}" (regex), got "{actual}"')
    else:
        raise ValueError(f"unknown mode {mode!r}; expected 'regex', 'equals', or 'contains'")
    return actual


async def _check_text(
    page: Any,
    selector: str,
    text: str,
    mode: str = "contains",
    timeout_ms: int | None = None,
) -> str:
    """Wait for *selector* and assert its inner text matches *text*.

    Returns the actual inner text on success.
    Raises ``RuntimeError`` on mismatch or timeout.
    """
    timeout = timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS
    try:
        element = await page.wait_for_selector(selector, timeout=timeout)
    except Exception as exc:
        raise RuntimeError(f'element never appeared within {timeout}ms: selector="{selector}"') from exc
    if element is None:
        raise RuntimeError(f'element never appeared within {timeout}ms: selector="{selector}"')
    actual: str = await element.inner_text()
    if mode == "contains":
        if text not in actual:
            raise RuntimeError(f'text mismatch on "{selector}": expected to contain "{text}", got "{actual}"')
    elif mode == "equals":
        if actual != text:
            raise RuntimeError(f'text mismatch on "{selector}": expected "{text}" (equals), got "{actual}"')
    elif mode == "regex":
        if not re.search(text, actual):
            raise RuntimeError(f'text mismatch on "{selector}": expected pattern "{text}" (regex), got "{actual}"')
    else:
        raise ValueError(f"unknown mode {mode!r}; expected 'contains', 'equals', or 'regex'")
    return actual


async def _check_selector(
    page: Any,
    selector: str,
    present: bool = True,
    timeout_ms: int | None = None,
) -> None:
    """Assert that *selector* is present (or absent) in the page.

    Raises ``RuntimeError`` if the condition is not met.
    """
    timeout = timeout_ms if timeout_ms is not None else DEFAULT_ACTION_TIMEOUT_MS
    if present:
        try:
            await page.wait_for_selector(selector, timeout=timeout)
        except Exception as exc:
            raise RuntimeError(f'selector never appeared within {timeout}ms: "{selector}"') from exc
    else:
        # Poll once — if the element exists right now, that's the failure.
        element = await page.query_selector(selector)
        if element is not None:
            raise RuntimeError(f'selector should be absent but was found: "{selector}"')


async def _check_js(page: Any, expression: str, equals: Any = None) -> Any:
    """Evaluate *expression* in the page and assert it is truthy (or equals *equals*).

    Returns the evaluated result on success.
    Raises ``RuntimeError`` on failure.
    """
    result = await page.evaluate(expression)
    if equals is not None:
        if result != equals:
            raise RuntimeError(f"JS assertion failed: expression={expression!r}, expected={equals!r}, got={result!r}")
    else:
        if not result:
            raise RuntimeError(f"JS assertion failed (not truthy): expression={expression!r}, got={result!r}")
    return result


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
