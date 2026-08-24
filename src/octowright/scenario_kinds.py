# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Which adapter, which pool, and what a kind can do.

Lives beside ``scenarios_pool`` rather than inside it because that module is
already 550 lines and every scenario task adds to it, and because "resolve a
participant's kind" is one responsibility with its own tests -- the same split
``http/routes/_session_kinds.py`` made for the HTTP layer.

Terminal is deliberately absent from the adapter path. It keeps its hardcoded
branch until step 5 extracts it, so ``adapter_for`` returns ``None`` for it and
callers fall through to that branch. It is NOT special-cased in
``capabilities_for``: a kind with no adapter supports nothing, which is already
the right answer for terminal and stays right when it becomes a plugin.
"""

from __future__ import annotations

from typing import Any

from octowright.defaults import SUPPORTED_KINDS
from octowright.plugins.contract import capabilities_of
from octowright.scenario_adapters import browser_scenario_adapter

#: The one place this literal is spelled. Callers ask for it by name so the
#: step-5 deletion is a search for one symbol rather than for a string.
TERMINAL_KIND = "terminal"


def _plugin_registry() -> Any:
    # ``plugins.state``, NOT ``server.plugin_state``: importing ``octowright.server``
    # runs its __init__, which imports every tool submodule (Playwright included)
    # to trigger @mcp.tool registration. This function is reached from
    # ``scenarios._validate_participant_kind`` -- core model code -- and going
    # through the tool layer took validating one participant from 392 to 1146
    # loaded modules. ``server.plugin_state`` re-exports these same functions, so
    # both paths share one global.
    from octowright.plugins.state import registry

    return registry()


def adapter_for(kind: str, *, browser_pool: Any) -> Any | None:
    """Return the scenario adapter for ``kind``, or ``None`` if it has none.

    ``None`` means "not adapter-driven", which today is terminal (hardcoded
    branch) and any unregistered kind. Callers must handle it rather than
    assume every participant has an adapter.

    ``None`` does NOT mean "invalid kind" -- terminal is a perfectly valid
    kind with no adapter. A caller that needs to validate a kind must use
    ``known_kinds()``, not the return value of this function.
    """
    if kind in SUPPORTED_KINDS:
        return browser_scenario_adapter(browser_pool)
    if kind == TERMINAL_KIND:
        return None
    registry = _plugin_registry()
    if kind in registry.kinds():
        return registry.get_plugin(kind).adapter
    return None


def capabilities_for(kind: str, *, browser_pool: Any) -> frozenset[str]:
    """What ``kind`` can do in a scenario, derived from its adapter.

    A kind with no adapter supports nothing. That is deliberately not a
    terminal special case -- it is the general rule, and terminal happens to be
    its most visible instance right now.
    """
    adapter = adapter_for(kind, browser_pool=browser_pool)
    return frozenset() if adapter is None else capabilities_of(adapter)


def supports(kind: str, capability: str, *, browser_pool: Any) -> bool:
    return capability in capabilities_for(kind, browser_pool=browser_pool)


def pool_for_kind(kind: str, *, browser_pool: Any, terminal_pool: Any | None) -> Any:
    """Resolve which pool owns sessions of ``kind``."""
    if kind in SUPPORTED_KINDS:
        return browser_pool
    if kind == TERMINAL_KIND:
        if terminal_pool is None:
            # Preserved from the _pool_for this replaces. Returning None would
            # turn a clear "the extra is not installed" error into an
            # AttributeError on None.close() deep inside teardown.
            raise RuntimeError("terminal participant present but terminal_pool is unavailable")
        return terminal_pool
    registry = _plugin_registry()
    pools = registry.pools()
    if kind not in pools:
        raise KeyError(f"no pool for scenario participant kind {kind!r}")
    return pools[kind]


def known_kinds(*, include_plugins: bool = True) -> list[str]:
    """Every kind a scenario participant may name, for error messages.

    Sorted so a validation failure reads the same on every machine -- entry
    point enumeration order is installation-dependent.

    A plugin kind is included only when its ``create_scenario_adapter``
    actually returned an adapter (spec 7.1). ``registry.kinds()`` lists every
    *loaded* plugin regardless, so a plugin registered with ``adapter=None``
    would otherwise pass kind validation here and then fail later at launch
    (``adapter_for`` returns ``None``, ``_launch_plugin_participants`` refuses
    it) -- a confusing two-step failure for what should be one clear
    "unsupported kind" error.
    """
    kinds = set(SUPPORTED_KINDS) | {TERMINAL_KIND}
    if include_plugins:
        registry = _plugin_registry()
        kinds |= {kind for kind in registry.kinds() if registry.get_plugin(kind).adapter is not None}
    return sorted(kinds)
