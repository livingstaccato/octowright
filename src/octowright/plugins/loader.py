# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Two-phase plugin load with rollback.

Phase one resolves descriptors and validates metadata. It must run **before**
the profile filter is computed, because a plugin's capability profile has to
be registered before any ``@mcp.tool`` decorator fires. Phase two imports the
plugin's tool module and builds its pool.

A plugin loads completely or not at all. Partial load is the failure mode
worth designing against: a plugin whose tools registered but whose pool does
not exist would answer MCP calls with internal errors forever.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from provide.telemetry import get_logger

from octowright.plugins.contract import PLUGIN_API_VERSION, SessionKindPlugin
from octowright.plugins.discovery import DiscoveredPlugin
from octowright.plugins.identity import validate_kind, validate_tool_names
from octowright.plugins.registry import PluginRegistry

log = get_logger("octowright.plugins.loader")


@dataclass(frozen=True)
class ResolvedDescriptor:
    discovered: DiscoveredPlugin
    descriptor: SessionKindPlugin


def _tool_names(tool_manager: Any) -> set[str]:
    """Snapshot the tool manager's registered names.

    Reaches into the SDK's private mapping. Narrow and deliberate: rolling back
    by declared name alone would leak an *undeclared* tool a module registered
    before raising, which is the one case rollback exists for.
    """
    return set(getattr(tool_manager, "_tools", {}))


def _remove_tools(tool_manager: Any, names: Iterable[str]) -> None:
    tools = getattr(tool_manager, "_tools", None)
    if tools is None:  # pragma: no cover - defensive
        return
    for name in names:
        tools.pop(name, None)


def resolve_descriptors(
    *,
    registry: PluginRegistry,
    discovered: list[DiscoveredPlugin],
    enabled: list[str],
    tool_manager: Any | None = None,
) -> list[ResolvedDescriptor]:
    """Import and validate descriptors for the enabled plugins only.

    Everything a *disabled* plugin reports comes from metadata; resolving its
    descriptor would execute exactly the code explicit enable exists to gate.

    ``tool_manager`` is optional here because this phase runs during
    ``_state`` import, *before* ``mcp`` exists and long before core's tool
    modules have registered anything — so there is nothing to collide with
    yet. Pass it in tests. The real core-collision check runs in
    :func:`activate`, which happens after core registration.
    """
    by_name = {found.name: found for found in discovered}
    for found in discovered:
        if found.name not in enabled:
            registry.record_state(name=found.name, state="disabled", discovered=found)

    core_tools = _tool_names(tool_manager) if tool_manager is not None else set()
    claimed: set[str] = set()
    resolved: list[ResolvedDescriptor] = []

    for name in enabled:
        entry = by_name.get(name)
        if entry is None:
            registry.record_state(name=name, state="missing")
            log.warning("octowright.plugins.enabled_but_not_installed", name=name)
            continue
        try:
            descriptor = entry.ep.load()
        except Exception as exc:
            registry.record_failure(name=name, reason=f"descriptor import failed: {exc!r}", discovered=entry)
            log.warning("octowright.plugins.descriptor_import_failed", name=name, error=repr(exc))
            continue
        try:
            _validate(descriptor, core_tools=core_tools, claimed=claimed)
            _ = core_tools  # empty during the _state phase; see the docstring
        except Exception as exc:
            registry.record_failure(
                name=name, reason=str(exc), discovered=entry, descriptor=_safe_descriptor(descriptor)
            )
            log.warning("octowright.plugins.validation_failed", name=name, error=str(exc))
            continue
        claimed |= set(descriptor.tool_names)
        resolved.append(ResolvedDescriptor(discovered=entry, descriptor=descriptor))

    return resolved


def _safe_descriptor(descriptor: Any) -> SessionKindPlugin | None:
    """Return the descriptor only if its metadata is readable enough to report."""
    required = ("kind", "display_name", "plugin_api_version", "tool_names")
    return descriptor if all(hasattr(descriptor, attr) for attr in required) else None


def _validate(descriptor: Any, *, core_tools: set[str], claimed: set[str]) -> None:
    if getattr(descriptor, "plugin_api_version", None) != PLUGIN_API_VERSION:
        raise ValueError(
            f"plugin_api_version {getattr(descriptor, 'plugin_api_version', None)!r} "
            f"does not match core's {PLUGIN_API_VERSION}"
        )
    validate_kind(descriptor.kind)
    validate_tool_names(descriptor.kind, frozenset(descriptor.tool_names))
    collisions = set(descriptor.tool_names) & (core_tools | claimed)
    if collisions:
        raise ValueError(f"tool name collision: {sorted(collisions)}")


def activate(
    *,
    registry: PluginRegistry,
    resolved: list[ResolvedDescriptor],
    ctx_factory: Callable[[str], Any],
    tool_manager: Any,
    import_module: Callable[[str], Any] | None = None,
) -> None:
    """Import each plugin's tool module and build its pool, or roll back."""
    importer = import_module or importlib.import_module

    registered = _tool_names(tool_manager)
    for item in resolved:
        descriptor = item.descriptor
        # The real collision check. resolve_descriptors could not run it: it
        # executes during `_state` import, before core's ~129 tools register.
        # Skipping it here would let a plugin claiming a non-reserved kind
        # (macro_run, capture_create, persona_get) SHADOW core's tool under the
        # SDK's first-wins add_tool, which is the inversion of the bug the
        # check exists to prevent.
        collisions = set(descriptor.tool_names) & registered
        if collisions:
            registry.record_failure(
                name=item.discovered.name,
                reason=f"tool name collision with an already-registered tool: {sorted(collisions)}",
                discovered=item.discovered,
                descriptor=_safe_descriptor(descriptor),
            )
            log.warning(
                "octowright.plugins.tool_collision",
                name=item.discovered.name,
                collisions=sorted(collisions),
            )
            continue
        before = _tool_names(tool_manager)
        delta: set[str] = set()
        pool = None
        try:
            if descriptor.tool_module:
                importer(descriptor.tool_module)
                delta = _tool_names(tool_manager) - before
            pool = descriptor.create_pool(ctx_factory(descriptor.kind))
            adapter = descriptor.create_scenario_adapter(pool)
            registry.register(descriptor, pool=pool, adapter=adapter, discovered=item.discovered)
            registered |= delta
        except Exception as exc:
            delta = delta or (_tool_names(tool_manager) - before)
            _remove_tools(tool_manager, delta)
            registry.record_failure(
                name=item.discovered.name,
                reason=f"activation failed: {exc!r}",
                discovered=item.discovered,
                descriptor=_safe_descriptor(descriptor),
            )
            log.warning(
                "octowright.plugins.activation_failed",
                name=item.discovered.name,
                error=repr(exc),
                rolled_back_tools=sorted(delta),
            )
