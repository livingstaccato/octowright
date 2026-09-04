# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Compiler for a friendly macro YAML DSL.

The compiler is intentionally pure:
no file I/O, no side effects, and all behavior is driven entirely by
arguments and return value.
"""

from __future__ import annotations

from typing import Any

import yaml

_SIMPLE_REQUIRED_FIELDS = {
    "navigate": ("url",),
    "click": ("selector",),
    "fill": ("selector", "value"),
}
_CONDITIONAL_ACTIONS = {"if_selector", "try", "try_each"}
_SHORTHAND_ACTION_FIELDS = {
    "navigate": "url",
    "click": "selector",
    "press_key": "key",
    "fill": None,
    "if_selector": None,
    "try": None,
    "try_each": None,
}


def parse_macro_yaml(text: str) -> dict[str, Any]:
    """Parse macro YAML into a Python mapping."""
    raw = yaml.safe_load(text)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"macro YAML must be a mapping, got {type(raw).__name__}")
    return raw


def compile_macro_yaml(text: str, *, name: str | None = None, strict: bool = True) -> dict[str, Any]:
    """Parse and compile DSL YAML text into the existing runtime JSON macro shape."""
    return compile_macro_document(parse_macro_yaml(text), name=name, strict=strict)


def _error(message: str, *, strict: bool) -> None:
    if strict:
        raise ValueError(message)


def _coerce_parameters(raw: Any, *, strict: bool) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        _error(f"macro 'parameters' must be a list, got {type(raw).__name__}", strict=strict)
        if strict:
            raise ValueError(f"macro 'parameters' must be a list, got {type(raw).__name__}")  # safety
        return []

    parameters: list[str] = []
    for value in raw:
        if isinstance(value, str):
            parameters.append(value)
            continue
        if strict:
            raise ValueError(f"macro parameter {value!r} must be a string")
        parameters.append(str(value))
    return parameters


def _as_list_of_actions(value: Any, path: str, *, strict: bool) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        _error(f"{path} must be a list, got {type(value).__name__}", strict=strict)
        return []
    out: list[dict[str, Any]] = []
    for idx, action in enumerate(value):
        item = _compile_action(action, f"{path}[{idx}]", strict=strict)
        if item is not None:
            out.append(item)
    return out


def _compile_if_selector(
    action: dict[str, Any],
    path: str,
    *,
    strict: bool,
) -> dict[str, Any]:
    if "selector" not in action:
        _error(f"{path}: if_selector is missing required field 'selector'", strict=strict)
    if "then" in action:
        then_raw = action.get("then")
        if not isinstance(then_raw, list):
            _error(f"{path}: if_selector.then must be a list, got {type(then_raw).__name__}", strict=strict)
            then: list[dict[str, Any]] = []
        else:
            then = _as_list_of_actions(then_raw, f"{path}.then", strict=strict)
        action["then"] = then
    if "else" in action:
        else_raw = action.get("else")
        if not isinstance(else_raw, list):
            _error(f"{path}: if_selector.else must be a list, got {type(else_raw).__name__}", strict=strict)
            else_branch: list[dict[str, Any]] = []
        else:
            else_branch = _as_list_of_actions(else_raw, f"{path}.else", strict=strict)
        action["else"] = else_branch
    return action


def _compile_try(
    action: dict[str, Any],
    path: str,
    *,
    strict: bool,
) -> dict[str, Any]:
    actions_raw = action.get("actions")
    if not isinstance(actions_raw, list):
        _error(f"{path}: try is missing required field 'actions' list", strict=strict)
        if strict:
            raise ValueError(f"{path}: try is missing required field 'actions' list")
        action["actions"] = []
        return action
    action["actions"] = _as_list_of_actions(actions_raw, f"{path}.actions", strict=strict)
    return action


def _compile_try_each(
    action: dict[str, Any],
    path: str,
    *,
    strict: bool,
) -> dict[str, Any]:
    branches_raw = action.get("branches")
    if not isinstance(branches_raw, list):
        _error(f"{path}: try_each is missing required field 'branches' list", strict=strict)
        if strict:
            raise ValueError(f"{path}: try_each is missing required field 'branches' list")
        action["branches"] = []
        return action

    branches: list[list[dict[str, Any]]] = []
    for idx, branch in enumerate(branches_raw):
        branch_path = f"{path}.branches[{idx}]"
        if not isinstance(branch, list):
            _error(f"{branch_path} must be a list, got {type(branch).__name__}", strict=strict)
            if strict:
                raise ValueError(f"{branch_path} must be a list, got {type(branch).__name__}")
            continue
        branches.append(_as_list_of_actions(branch, branch_path, strict=strict))
    action["branches"] = branches
    return action


def _validate_required_simple(action: dict[str, Any], kind: str, path: str, *, strict: bool) -> None:
    for field in _SIMPLE_REQUIRED_FIELDS[kind]:
        if action.get(field) in (None, ""):
            _error(f"{path}: {kind} is missing required field '{field}'", strict=strict)


def _expand_scalar_shorthand(key: str, value: Any) -> dict[str, Any] | None:
    """`{key: scalar}` shorthand for navigate/click/press_key — returns the
    expanded {"action": key, <field>: value} or None if the kind is wrong."""
    field = _SHORTHAND_ACTION_FIELDS.get(key)
    if not isinstance(field, str):
        return None
    return {"action": key, field: value}


def _expand_mapping_shorthand(key: str, value: Any, path: str, *, strict: bool) -> dict[str, Any]:
    """`{key: {...}}` shorthand for fill / if_selector / try / try_each."""
    if not isinstance(value, dict):
        msg = (
            f"{path}: fill shorthand requires a mapping payload"
            if key == "fill"
            else f"{path}: {key} shorthand must be a mapping, got {type(value).__name__}"
        )
        _error(msg, strict=strict)
        if strict:
            raise ValueError(msg)
        return {key: value}
    return {"action": key, **value}


def _normalize_shorthand(node: dict[str, Any], path: str, *, strict: bool) -> dict[str, Any]:
    if len(node) != 1:
        return node
    key, value = next(iter(node.items()))
    if key not in _SHORTHAND_ACTION_FIELDS:
        return node
    if key in {"navigate", "click", "press_key"}:
        return _expand_scalar_shorthand(key, value) or node
    if key in {"fill", "if_selector", "try", "try_each"}:
        return _expand_mapping_shorthand(key, value, path, strict=strict)
    return node


# Conditional-action compilers — keyed by action name.
_CONDITIONAL_COMPILERS: dict[str, Any] = {}


def _register_conditional_compilers() -> None:
    """Late-binding registration: the compilers are defined elsewhere in this
    module; we wire them into the dispatch table here so _compile_action stays
    a thin lookup."""
    _CONDITIONAL_COMPILERS["if_selector"] = _compile_if_selector
    _CONDITIONAL_COMPILERS["try"] = _compile_try
    _CONDITIONAL_COMPILERS["try_each"] = _compile_try_each


def _compile_action(action: Any, path: str, *, strict: bool) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        _error(f"{path}: action must be an object, got {type(action).__name__}", strict=strict)
        return None

    action_obj = dict(action)
    action_obj = _normalize_shorthand(action_obj, path, strict=strict)

    if not isinstance(action_obj.get("action"), str):
        _error(f"{path}: missing or invalid 'action' field", strict=strict)
        return action_obj

    kind = action_obj["action"]
    compiler = _CONDITIONAL_COMPILERS.get(kind)
    if compiler is not None:
        return compiler(action_obj, path, strict=strict)
    if kind in _SIMPLE_REQUIRED_FIELDS:
        _validate_required_simple(action_obj, kind, path, strict=strict)
    # Unknown or server-defined action keys pass through.
    return action_obj


_register_conditional_compilers()


def compile_macro_document(
    doc: Any,
    *,
    name: str | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Compile a parsed DSL document into the runtime macro shape."""
    if not isinstance(doc, dict):
        _error(f"macro document must be a mapping, got {type(doc).__name__}", strict=strict)
        if strict:
            raise ValueError(f"macro document must be a mapping, got {type(doc).__name__}")
        doc = {}

    raw_name = name if name is not None else doc.get("name")
    if raw_name is None:
        raw_name = "macro"

    raw_actions = doc.get("actions")
    if not isinstance(raw_actions, list):
        _error("macro is missing required field 'actions'", strict=strict)
        if strict:
            raise ValueError("macro is missing required field 'actions'")
        raw_actions = []

    return {
        "name": str(raw_name),
        "description": doc.get("description"),
        "parameters": _coerce_parameters(doc.get("parameters"), strict=strict),
        "actions": _as_list_of_actions(raw_actions, "actions", strict=strict),
    }
