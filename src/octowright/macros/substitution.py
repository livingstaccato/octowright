# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import copy
import re
from typing import Any

SEMANTIC_LOCATOR_KEYS = (
    "role",
    "role_name",
    "label",
    "text",
    "test_id",
    "role_exact",
    "label_exact",
    "text_exact",
)
# Keys that carry no human-readable ARIA text and so are noise in a digest.
# The *_exact flags are modifiers on another key, never a name themselves.
NON_ARIA_NOISE_KEYS = ("role", "role_name", "test_id", "role_exact", "label_exact", "text_exact")
RECORDING_NOISE_KEYS = ("action", "ts", "kind", "profile", "instance_id")


def normalise_parameters(parameters: list[str] | dict[str, str] | None) -> dict[str, str]:
    if parameters is None:
        return {}
    if isinstance(parameters, dict):
        return parameters
    return {f"params[{i}]": v for i, v in enumerate(parameters)}


def substitute_in_action(action: dict[str, Any], value_to_name: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in action.items():
        if isinstance(value, str) and value in value_to_name:
            result[key] = "{{" + value_to_name[value] + "}}"
        else:
            result[key] = value
    return result


def action_kwargs(action: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in action.items() if key not in RECORDING_NOISE_KEYS}


#: Actions whose locator IS the semantic keys, so stripping them would remove
#: the thing the action matches on. Enumerated rather than inferred, and it has
#: already been missed once: get_text_by was absent, so replaying one called
#: session.get_text_by() with no finder at all.
_SEMANTIC_ACTIONS = {"click", "fill", "click_by", "fill_by", "get_text_by"}


def strip_non_aria_noise(kind: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    if kind in _SEMANTIC_ACTIONS:
        return dict(kwargs)

    cleaned = dict(kwargs)
    for key in NON_ARIA_NOISE_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _substitute_value(value: Any, args: dict[str, Any]) -> Any:
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in args:
                raise KeyError(f"placeholder {{{{{key}}}}} has no matching arg; available: {list(args)}")
            return str(args[key])

        return re.sub(r"\{\{([^}]+)\}\}", replacer, value)
    if isinstance(value, dict):
        return {key: _substitute_value(item, args) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_value(item, args) for item in value]
    return value


def substitute(actions: list[dict[str, Any]], args: dict[str, Any]) -> list[dict[str, Any]]:
    return [_substitute_value(copy.deepcopy(action), args) for action in actions]
