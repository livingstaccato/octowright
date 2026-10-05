# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A macro's own word on which parameters are sensitive: ``parameter_specs`` (#248, Part B).

Arguments are classified by name (`privacy`), and a name says too little in
both directions. ``display`` holding a national id is not recognised, and
``username`` -- which an application's header draws on every page -- cannot be
shown, so a redacted screenshot of any signed-in page is refused. A macro can
now say so, per top-level parameter::

    {"parameters": ["email", "password", "display", "username"],
     "parameter_specs": {"display": {"sensitive": true}, "username": {"sensitive": false}}}

Resolution, per top-level argument: ``parameter_specs[name]["sensitive"]`` when
it is present and a real ``bool``, otherwise the name heuristic. A nested key
is never named by a spec; it inherits its branch, and a key under a public
parameter is still classified by its own name.

- ``sensitive: true`` makes the parameter credential-tier everywhere, exactly
  as a ``{"credential": ...}`` sequence argument is: redacted and scrubbed for
  the session, held to the credential sink guards and the fill-origin check,
  and a run that types it runs no page code.
- ``sensitive: false`` unmarks an identity or contextual name (``username``,
  ``email``, ``session``...). It has a floor it cannot go below: a name that
  reads as a credential (`privacy.is_credential_key`), a value the caller
  passed as a credential, and the text an ``expect_no_text`` checks for stay
  credential-tier, and each ignored unmark is reported (`ResolvedPrivacy.warnings`).
  So ``sensitive: false`` never loosens a sink guard; only
  ``OCTOWRIGHT_MACRO_CREDENTIAL_SINKS`` does.

A spec that is not shaped as above is ignored and reported, and the parameters
it would have covered keep their name classification: the run still happens,
classified as it was before ``parameter_specs`` existed.

Warnings carry parameter names, never values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from octowright.macros.privacy import MacroArgPrivacy, assertion_text_args, is_credential_key

#: The macro key.
SPECS_KEY = "parameter_specs"
#: The one field a spec carries today.
SENSITIVE_KEY = "sensitive"


@dataclass(frozen=True)
class DeclaredSensitivity:
    """What a macro's ``parameter_specs`` says, before the floor is applied."""

    sensitive: frozenset[str] = frozenset()
    public: frozenset[str] = frozenset()
    #: Shape problems, each naming where, never a value.
    problems: tuple[str, ...] = ()


def _read_spec(name: Any, spec: Any) -> bool | str | None:
    """One entry: its ``sensitive`` flag, ``None`` when it declares nothing, or the problem."""
    if not isinstance(name, str) or not name:
        return f"{SPECS_KEY} has a key that is not a parameter name; it was ignored"
    if not isinstance(spec, Mapping):
        return f'{SPECS_KEY}[{name!r}] must be an object such as {{"{SENSITIVE_KEY}": true}}; ignored'
    flag = spec.get(SENSITIVE_KEY)
    if SENSITIVE_KEY not in spec or isinstance(flag, bool):
        return flag
    return f"{SPECS_KEY}[{name!r}].{SENSITIVE_KEY} must be true or false; {name!r} is classified by its name"


def declared_sensitivity(macro: Any) -> DeclaredSensitivity:
    """Read *macro*'s ``parameter_specs``; anything malformed is reported and ignored, never raised."""
    raw = macro.get(SPECS_KEY) if isinstance(macro, Mapping) else None
    if raw is None:
        return DeclaredSensitivity()
    if not isinstance(raw, Mapping):
        return DeclaredSensitivity(
            problems=(
                f"{SPECS_KEY} must be an object mapping parameter names to "
                f'{{"{SENSITIVE_KEY}": true|false}}; it was ignored, so every parameter is classified by its name',
            )
        )
    return _collected({name: _read_spec(name, spec) for name, spec in raw.items()})


def _collected(read: dict[Any, bool | str | None]) -> DeclaredSensitivity:
    return DeclaredSensitivity(
        sensitive=frozenset(name for name, flag in read.items() if flag is True),
        public=frozenset(name for name, flag in read.items() if flag is False),
        problems=tuple(flag for flag in read.values() if isinstance(flag, str)),
    )


def declared_sensitive_names(macro: Any) -> frozenset[str]:
    """The parameters *macro* declares sensitive: credential-tier wherever they go."""
    return declared_sensitivity(macro).sensitive


@dataclass(frozen=True)
class ResolvedPrivacy:
    """One macro's privacy view, resolved from the dict that executes.

    ``credential_args`` is what the sink guards, the fill-origin check and the
    page-code refusal treat as credentials whatever they are named: the
    caller's credential-origin arguments and the declared-sensitive ones.
    """

    privacy: MacroArgPrivacy
    credential_args: frozenset[str]
    #: Everything to report: the spec's shape problems, then `ignored`.
    warnings: tuple[str, ...] = ()
    #: The ``sensitive: false`` declarations the floor held down.
    ignored: tuple[str, ...] = ()


def _floor_reason(name: str, *, origin: frozenset[str], assertion: frozenset[str]) -> str | None:
    if is_credential_key(name):
        return "its name reads as a credential"
    if name in origin:
        return 'its value was passed as a credential ({"credential": ...})'
    if name in assertion:
        return "it is the text an expect_no_text step checks for"
    return None


def resolve_macro_privacy(macro: Any, *, credential_args: frozenset[str] = frozenset()) -> ResolvedPrivacy:
    """*macro*'s view: name and position, then its declarations, then the floor.

    *credential_args* are the caller's credential-origin arguments (a
    sequence's ``{"credential": ...}``, a caller's credential passed to a
    ``macro_call``); origin wins over a declaration. Never raises on a
    malformed spec: that is reported in ``warnings`` and the name heuristic
    applies instead.
    """
    actions = macro.get("actions", []) if isinstance(macro, Mapping) else []
    assertion = assertion_text_args(actions)
    origin = frozenset(credential_args)
    declared = declared_sensitivity(macro)
    label = macro.get("name") if isinstance(macro, Mapping) else None
    prefix = f"macro {label!r}: " if isinstance(label, str) and label else ""
    public: set[str] = set()
    ignored: list[str] = []
    for name in sorted(declared.public):
        reason = _floor_reason(name, origin=origin, assertion=assertion)
        if reason is None:
            public.add(name)
        else:
            ignored.append(
                f"{prefix}{SPECS_KEY} marks {name!r} not sensitive, but {reason}, so it stays credential-tier"
            )
    credentials = origin | declared.sensitive
    privacy = MacroArgPrivacy(assertion_args=assertion, credential_args=credentials, public_args=frozenset(public))
    warnings = (*(prefix + problem for problem in declared.problems), *ignored)
    return ResolvedPrivacy(privacy=privacy, credential_args=credentials, warnings=warnings, ignored=tuple(ignored))


def macro_privacy(macro: Any, *, credential_args: frozenset[str] = frozenset()) -> MacroArgPrivacy:
    """`resolve_macro_privacy`'s view alone, for a record that does not report warnings."""
    return resolve_macro_privacy(macro, credential_args=credential_args).privacy
