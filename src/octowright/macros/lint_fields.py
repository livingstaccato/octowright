# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-action ALLOWED field sets, derived from the real dispatch pipeline.

``lint.py``'s ``_SIMPLE_REQUIRED`` answers "which fields must be present?" and
nothing answers "which fields are even accepted?". Since dispatch ends in
``getattr(session, method)(**kwargs)``, an unrecognised key is a guaranteed
``TypeError`` — but only on the first LIVE replay, long after authoring, and
only after the macro's earlier actions have already run their side effects.

The obvious fix — a second hand-written table of allowed names — is the failure
mode this repo has already been bitten by: ``RECORDER_NOISE`` drifted from the
recorder and turned passive rows into 608 bogus replay errors. So the allowed
set is DERIVED. The first version of this module derived it from three sources
and still drifted, because ``dispatch_simple`` applies **five** transformations
and modelling only some of them is its own kind of hand-written table:

1. ``action_kwargs`` strips ``RECORDING_NOISE_KEYS`` — recorder/scenario
   bookkeeping that is never an input to anything;
2. ``strip_non_aria_noise`` pops ``NON_ARIA_NOISE_KEYS`` for every kind OUTSIDE
   ``_SEMANTIC_ACTIONS``, so a recorded ``type``/``wait_for`` legitimately
   carries the ``role``/``role_name`` the recorder stamped on it;
3. ``_dispatch_click_or_fill`` intercepts click/fill/click_by/fill_by BEFORE
   any signature is consulted: semantic keys go to ``click_by``/``fill_by``
   (which take ``**finders``) and everything else falls back to
   ``click``/``fill`` — so this family's contract is not its ``_ACTION_MAP``
   signature. ``timeout_ms`` was once accepted by one and popped by the other,
   and this module papered over the asymmetry with a hardcoded literal rather
   than reporting it; both halves now take the field, so it derives cleanly;
4. ``_REPLAY_RENAME_KEYS`` — recorded names the runtime renames before the
   call, so the RECORDED spelling is what a macro legitimately carries;
5. ``_REPLAY_DROP_KEYS`` — recorded-only observations the runtime strips, which
   are valid in a macro precisely because they never reach the method.

Getting this wrong is not a harmless warning. ``unknown_field`` is
error-severity, and the dashboard macro editor refuses to save while
``error_count`` is non-zero (``PUT /api/macros/{name}`` returns 400), so a
missed transformation makes recording-derived macros unsavable. The companion
test dispatches every allowed field through the real ``dispatch_simple``
against ``BrowserSession``'s own signatures, in both directions.

A method that takes ``**finders`` (the semantic click_by/fill_by/get_text_by
family) has no enumerable parameter list, so its finder keys come from
``SEMANTIC_LOCATOR_KEYS`` — the same tuple the substitution layer uses.
"""

from __future__ import annotations

import inspect

from octowright.macros.substitution import (
    _SEMANTIC_ACTIONS,
    NON_ARIA_NOISE_KEYS,
    RECORDING_NOISE_KEYS,
    SEMANTIC_LOCATOR_KEYS,
)

from .runtime import _ACTION_MAP, _REPLAY_DROP_KEYS, _REPLAY_RENAME_KEYS

#: Bookkeeping every action may carry regardless of its dispatch target,
#: because ``action_kwargs`` removes it before anything else runs. Derived, not
#: listed: an entry added to the strip is allowed here automatically.
#:
#: Deliberately NOT extended with annotation-ish names (``comment``,
#: ``description``, ``name``, ``optional``). Nothing in the pipeline strips
#: those, so they reach the session method and raise — blessing them would make
#: this check green-light the exact failure it exists to catch. ``name`` is the
#: sharpest: it is Playwright's own spelling of octowright's ``role_name``, and
#: because it is not a semantic key the click dispatcher filters it out and
#: clicks the FIRST matching element instead of raising.
_UNIVERSAL_FIELDS: frozenset[str] = frozenset(RECORDING_NOISE_KEYS)

#: Kinds ``dispatch_simple`` routes to ``_dispatch_click_or_fill`` instead of
#: to ``_ACTION_MAP``'s signature.
_CLICK_OR_FILL_KINDS = frozenset({"click", "fill", "click_by", "fill_by"})


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


def _click_or_fill_allowed(kind: str) -> set[str] | None:
    """Allowed fields for the click/fill family, which never consults ``_ACTION_MAP``.

    ``_dispatch_click_or_fill`` splits the action in two: keys in
    ``SEMANTIC_LOCATOR_KEYS`` (plus ``value`` for a fill) go to the
    ``**finders`` method, and everything left over is splatted at plain
    ``click``/``fill``. So the accepted set is the union, and the CSS-fallback
    method is the only signature that constrains it.

    ``timeout_ms`` used to be added here as a LITERAL, because the fallback
    popped it and so it appeared in no signature. That hardcoding is exactly
    what let lint bless a field the runtime discarded -- the drift this module
    exists to prevent, reintroduced by hand inside the module itself. Now that
    ``click``/``fill`` take it, the derivation covers it with nothing listed.
    """
    fallback = "fill" if kind in {"fill", "fill_by"} else "click"
    introspected = _session_method_params(fallback)
    if introspected is None:  # pragma: no cover - BrowserSession always has both
        return None
    params, _ = introspected
    return set(params) | set(SEMANTIC_LOCATOR_KEYS)


def _standard_allowed(kind: str, method_name: str) -> set[str] | None:
    """Allowed fields for everything dispatched through ``_dispatch_standard``."""
    introspected = _session_method_params(method_name)
    if introspected is None:
        return None
    params, takes_kwargs = introspected
    allowed = set(params)

    # A **finders method accepts the semantic locator vocabulary -- but NOT a
    # CSS `selector`. That fallback belongs to _dispatch_click_or_fill (handled
    # in _click_or_fill_allowed); the only **finders method routed here is
    # get_text_by, whose kwargs go straight to build_locator, which has no
    # `selector` parameter. Blessing it here made the lint miss a real
    # TypeError, and the signature probe cannot catch it because `selector`
    # binds happily as a **finders keyword and only explodes one level deeper.
    if takes_kwargs:
        allowed.update(SEMANTIC_LOCATOR_KEYS)

    # strip_non_aria_noise pops these before dispatch for any non-semantic
    # kind, so the recorder is free to stamp them on a `type` or a `wait_for`.
    if kind not in _SEMANTIC_ACTIONS:
        allowed.update(NON_ARIA_NOISE_KEYS)

    # Recorded spellings the runtime renames on the way to the method.
    allowed.update(_REPLAY_RENAME_KEYS.get(kind, {}))
    # Recorded-only observations the runtime strips before dispatch.
    allowed.update(_REPLAY_DROP_KEYS.get(kind, ()))
    return allowed


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

    allowed = _click_or_fill_allowed(kind) if kind in _CLICK_OR_FILL_KINDS else _standard_allowed(kind, method_name)
    if allowed is None:
        return frozenset()

    return frozenset(allowed | _UNIVERSAL_FIELDS)


def ambiguous_rename_fields(kind: str, action_keys: frozenset[str]) -> list[tuple[str, str]]:
    """Recorded/parameter spelling pairs an action carries BOTH of.

    ``_REPLAY_RENAME_KEYS`` makes the recorded spelling legal alongside the
    method's own parameter name, and nothing enforces that only one is present.
    ``_normalize_replay_kwargs`` builds ``{rename_map.get(k, k): v ...}``, so
    with both present the winner is decided by dict insertion order -- a
    formatter or a diff merge can silently change which value is installed.
    """
    pairs = [
        (recorded, param)
        for recorded, param in _REPLAY_RENAME_KEYS.get(kind, {}).items()
        if recorded in action_keys and param in action_keys
    ]
    return sorted(pairs)


def unknown_fields(kind: str, action_keys: frozenset[str]) -> frozenset[str]:
    """Fields in *action_keys* that *kind* cannot dispatch. Empty when unknown."""
    allowed = allowed_fields_for(kind)
    if not allowed:
        return frozenset()
    return frozenset(action_keys) - allowed
