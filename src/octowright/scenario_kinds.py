# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Which adapter, which pool, and what a kind can do.

Lives beside ``scenarios_pool`` rather than inside it because that module is
already 550 lines and every scenario task adds to it, and because "resolve a
participant's kind" is one responsibility with its own tests -- the same split
``http/routes/_session_kinds.py`` made for the HTTP layer.

Core knows exactly two kind families: a browser engine (``SUPPORTED_KINDS``)
and whatever a registered session-kind plugin claims. Terminal was the last
kind with a hardcoded branch here; step 5 removed it, so every non-browser
kind -- including terminal, when its plugin is enabled -- now resolves purely
through the plugin registry. A kind with no adapter supports nothing, which
is the general rule, not a per-kind special case.
"""

from __future__ import annotations

from typing import Any

from octowright.defaults import SUPPORTED_KINDS
from octowright.plugins.contract import capabilities_of
from octowright.scenario_adapters import browser_scenario_adapter


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

    ``None`` means "not adapter-driven": an unregistered kind, or a
    registered plugin whose ``create_scenario_adapter`` itself returned
    ``None``. Callers must handle it rather than assume every participant has
    an adapter.

    ``None`` does NOT always mean "invalid kind" -- a registered plugin may
    deliberately have no adapter. A caller that needs to validate a kind must
    use ``known_kinds()``, not the return value of this function.
    """
    if kind in SUPPORTED_KINDS:
        return browser_scenario_adapter(browser_pool)
    registry = _plugin_registry()
    if kind in registry.kinds():
        return registry.get_plugin(kind).adapter
    return None


def capabilities_for(kind: str, *, browser_pool: Any) -> frozenset[str]:
    """What ``kind`` can do in a scenario, derived from its adapter.

    A kind with no adapter supports nothing -- the general rule, with no
    per-kind special case.
    """
    adapter = adapter_for(kind, browser_pool=browser_pool)
    return frozenset() if adapter is None else capabilities_of(adapter)


def supports(kind: str, capability: str, *, browser_pool: Any) -> bool:
    return capability in capabilities_for(kind, browser_pool=browser_pool)


def pool_for_kind(kind: str, *, browser_pool: Any) -> Any:
    """Resolve which pool owns sessions of ``kind``."""
    if kind in SUPPORTED_KINDS:
        return browser_pool
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
    kinds = set(SUPPORTED_KINDS)
    if include_plugins:
        registry = _plugin_registry()
        kinds |= {kind for kind in registry.kinds() if registry.get_plugin(kind).adapter is not None}
    return sorted(kinds)
