# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-action ALLOWED field sets, derived from the real dispatch targets.

``lint.py``'s ``_SIMPLE_REQUIRED`` answers "which fields must be present?" and
nothing answers "which fields are even accepted?". Since
``runtime._dispatch_standard`` ends in ``getattr(session, method)(**kwargs)``,
an unrecognised key is a guaranteed ``TypeError`` — but only on the first LIVE
replay, long after authoring, and only after the macro's earlier actions have
already run their side effects.

The obvious fix — a second hand-written table of allowed names — is the
failure mode this repo has already been bitten by: ``RECORDER_NOISE`` drifted
from the recorder and turned passive rows into 608 bogus replay errors. So the
allowed set is DERIVED here, from the same three sources replay itself uses:

1. the dispatch target's signature (``_ACTION_MAP`` -> ``BrowserSession``
   method parameters);
2. ``_REPLAY_RENAME_KEYS`` — recorded names the runtime renames before the
   call, so the RECORDED spelling is what a macro legitimately carries;
3. ``_REPLAY_DROP_KEYS`` — recorded-only observations the runtime strips, which
   are valid in a macro precisely because they never reach the method.

A method that takes ``**finders`` (the semantic click_by/fill_by/get_text_by
family) has no enumerable parameter list, so its finder keys come from
``SEMANTIC_LOCATOR_KEYS`` — the same tuple the substitution layer uses.
"""

from __future__ import annotations

import inspect

from octowright.macros.substitution import RECORDING_NOISE_KEYS, SEMANTIC_LOCATOR_KEYS

from .runtime import _ACTION_MAP, _REPLAY_DROP_KEYS, _REPLAY_RENAME_KEYS

# Bookkeeping every action may carry regardless of its dispatch target: the
# recorder stamps these, and macro authors legitimately annotate with them.
_UNIVERSAL_FIELDS: frozenset[str] = frozenset(RECORDING_NOISE_KEYS) | {
    "action",
    "ts",
    "comment",
    "description",
    "name",
    "optional",
}


def _session_method_params(method_name: str) -> tuple[frozenset[str], bool] | None:
    """Parameter names of a ``BrowserSession`` method, and whether it takes **kwargs.

    ``None`` means "could not introspect" — distinct from a real zero-parameter
    method like ``navigate_back``, which accepts nothing yet is still a perfectly
    known action whose recorded-only fields must be allowed through.
    """
    from octowright.session import BrowserSession

    method = getattr(BrowserSession, method_name, None)
    if method is None:
        return None
    try:
        sig = inspect.signature(method)
    except (TypeError, ValueError):  # pragma: no cover - builtins/C methods
        return None

    names: set[str] = set()
    takes_kwargs = False
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            takes_kwargs = True
            continue
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        names.add(name)
    return frozenset(names), takes_kwargs


def allowed_fields_for(kind: str) -> frozenset[str]:
    """Field names a macro action of *kind* may carry.

    Returns an empty set for an action this module cannot reason about, which
    callers must treat as "don't check" rather than "nothing is allowed".

    Not cached: lint runs once per macro save over a handful of actions, so
    the introspection cost is negligible, and caching a function this small
    only buys a footgun -- it lets the *first* test to touch a given `kind`
    quietly own that line's coverage for every later test in the same run.
    """
    method_name = _ACTION_MAP.get(kind)
    if method_name is None:
        return frozenset()

    introspected = _session_method_params(method_name)
    if introspected is None:
        return frozenset()
    params, takes_kwargs = introspected

    allowed = set(params) | _UNIVERSAL_FIELDS

    # Recorded spellings the runtime renames on the way to the method.
    for recorded, _param in _REPLAY_RENAME_KEYS.get(kind, {}).items():
        allowed.add(recorded)

    # Recorded-only observations the runtime strips before dispatch.
    allowed.update(_REPLAY_DROP_KEYS.get(kind, ()))

    # A **finders method accepts the semantic locator vocabulary, plus the
    # CSS `selector` the runtime falls back to when the semantic path fails.
    if takes_kwargs:
        allowed.update(SEMANTIC_LOCATOR_KEYS)
        allowed.add("selector")

    return frozenset(allowed)


def unknown_fields(kind: str, action_keys: frozenset[str]) -> frozenset[str]:
    """Fields in *action_keys* that *kind* cannot dispatch. Empty when unknown."""
    allowed = allowed_fields_for(kind)
    if not allowed:
        return frozenset()
    return frozenset(action_keys) - allowed
