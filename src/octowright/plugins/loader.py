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

import asyncio
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

    No default: if the SDK renames the attribute, an empty snapshot would make
    every delta empty and every rollback a no-op — the half-registered plugin
    this machinery exists to prevent, arrived at silently. Raising instead is
    loud, and ``tests/plugins/test_tool_manager_coupling.py`` pins the shape.
    """
    return set(tool_manager._tools)


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
    _record_non_loading_states(registry, discovered, enabled)

    core_tools = _tool_names(tool_manager) if tool_manager is not None else set()
    claimed: set[str] = set()
    claimed_kinds: set[str] = set()
    resolved: list[ResolvedDescriptor] = []

    for name in enabled:
        entry = by_name.get(name)
        if entry is None:
            registry.record_state(name=name, state="missing")
            log.warning("octowright.plugins.enabled_but_not_installed", name=name)
            continue
        if entry.conflict:
            continue  # already recorded failed by _record_non_loading_states
        item = _resolve_one(registry, entry, core_tools=core_tools, claimed=claimed, claimed_kinds=claimed_kinds)
        if item is None:
            continue
        # Accumulate only for descriptors that PASSED, so one refused plugin
        # cannot make a later, legitimate one collide with it.
        claimed |= set(item.descriptor.tool_names)
        claimed_kinds.add(item.descriptor.kind)
        resolved.append(item)

    return resolved


def _record_non_loading_states(
    registry: PluginRegistry,
    discovered: list[DiscoveredPlugin],
    enabled: list[str],
) -> None:
    """Record every plugin that will not be resolved: conflicted or disabled."""
    for found in discovered:
        if found.conflict:
            # Two distributions claim this name, so it is unloadable whether or
            # not it was enabled — but it is still reported, and every other
            # plugin still loads.
            registry.record_failure(name=found.name, reason=found.conflict, discovered=found)
        elif found.name not in enabled:
            registry.record_state(name=found.name, state="disabled", discovered=found)


def _resolve_one(
    registry: PluginRegistry,
    entry: DiscoveredPlugin,
    *,
    core_tools: set[str],
    claimed: set[str],
    claimed_kinds: set[str],
) -> ResolvedDescriptor | None:
    """Import and validate one enabled plugin's descriptor, or record why not."""
    try:
        descriptor = entry.ep.load()
    except Exception as exc:
        registry.record_failure(name=entry.name, reason=f"descriptor import failed: {exc!r}", discovered=entry)
        log.warning("octowright.plugins.descriptor_import_failed", name=entry.name, error=repr(exc))
        return None
    try:
        _validate(descriptor, core_tools=core_tools, claimed=claimed, claimed_kinds=claimed_kinds)
    except Exception as exc:
        registry.record_failure(
            name=entry.name, reason=str(exc), discovered=entry, descriptor=_safe_descriptor(descriptor)
        )
        log.warning("octowright.plugins.validation_failed", name=entry.name, error=str(exc))
        return None
    return ResolvedDescriptor(discovered=entry, descriptor=descriptor)


def _safe_descriptor(descriptor: Any) -> SessionKindPlugin | None:
    """Return the descriptor only if its metadata is readable enough to report."""
    required = ("kind", "display_name", "plugin_api_version", "tool_names")
    return descriptor if all(hasattr(descriptor, attr) for attr in required) else None


def _validate(descriptor: Any, *, core_tools: set[str], claimed: set[str], claimed_kinds: set[str]) -> None:
    if getattr(descriptor, "plugin_api_version", None) != PLUGIN_API_VERSION:
        raise ValueError(
            f"plugin_api_version {getattr(descriptor, 'plugin_api_version', None)!r} "
            f"does not match core's {PLUGIN_API_VERSION}"
        )
    validate_kind(descriptor.kind)
    # Kind must be unique across enabled plugins. The registry is keyed by
    # kind, so a second claimant would silently replace the first's pool —
    # dropping it without close_all while both still report `enabled` (status
    # rows are keyed by *name*) and the first plugin's already-registered
    # tools resolve through `pool_for(kind)` to the second plugin's pool.
    if descriptor.kind in claimed_kinds:
        raise ValueError(f"kind collision: {descriptor.kind!r} is already claimed by an enabled plugin")
    validate_tool_names(descriptor.kind, frozenset(descriptor.tool_names))
    collisions = set(descriptor.tool_names) & (core_tools | claimed)
    if collisions:
        raise ValueError(f"tool name collision: {sorted(collisions)}")


def _abandon_pool(pool: Any, name: str) -> None:
    """Best-effort teardown of a pool created but never registered.

    ``close_all`` is async while ``activate`` is not — it runs at module-import
    time, where there is no running loop, so ``asyncio.run`` completes it. Under
    an already-running loop we cannot block, and a fire-and-forget task would
    only trade the leak for an unawaited-task warning, so that case is logged.
    """
    if pool is None:
        return
    try:
        coro = pool.close_all(force=True)
    except Exception as exc:
        log.warning("octowright.plugins.abandoned_pool_close_failed", name=name, error=repr(exc))
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception as exc:
            log.warning("octowright.plugins.abandoned_pool_close_failed", name=name, error=repr(exc))
        return
    coro.close()
    log.warning("octowright.plugins.abandoned_pool_not_closed", name=name, reason="event loop already running")


def activate(
    *,
    registry: PluginRegistry,
    resolved: list[ResolvedDescriptor],
    ctx_factory: Callable[[str], Any],
    tool_manager: Any,
    import_module: Callable[[str], Any] | None = None,
    on_rollback: Callable[[SessionKindPlugin], None] | None = None,
) -> None:
    """Import each plugin's tool module and build its pool, or roll back.

    ``on_rollback`` is invoked with the descriptor of a plugin whose
    activation failed, after its tools and pool are unwound. It exists so the
    caller can unregister the plugin's capability profile: this module sits
    below ``server/`` and must not import ``server.profiles``, but leaving a
    failed plugin's profile registered would make ``OCTOWRIGHT_PROFILE=<its
    name>`` resolve to a set naming tools that do not exist.
    """
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
                _check_delta(item.discovered.name, descriptor, delta)
            pool = descriptor.create_pool(ctx_factory(descriptor.kind))
            adapter = descriptor.create_scenario_adapter(pool)
            registry.register(descriptor, pool=pool, adapter=adapter, discovered=item.discovered)
            registered |= delta
        except Exception as exc:
            delta = delta or (_tool_names(tool_manager) - before)
            _remove_tools(tool_manager, delta)
            _abandon_pool(pool, item.discovered.name)
            _run_rollback_hook(on_rollback, descriptor, item.discovered.name)
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


def _check_delta(name: str, descriptor: SessionKindPlugin, delta: set[str]) -> None:
    """Reconcile what the tool module actually registered against what it declared.

    A tool outside ``tool_names`` is an error: the declaration is what the
    collision check in :func:`_validate` reasoned about, so an undeclared
    registration went through no collision check at all. A *smaller* delta is
    legitimate — the active capability profile suppresses tools the plugin
    declared — so it is logged rather than refused.
    """
    declared = set(descriptor.tool_names)
    undeclared = delta - declared
    if undeclared:
        raise ValueError(f"tool module registered undeclared tools: {sorted(undeclared)}")
    filtered = declared - delta
    if filtered:
        log.info("octowright.plugins.tools_filtered_by_profile", name=name, not_registered=sorted(filtered))


def _run_rollback_hook(
    hook: Callable[[SessionKindPlugin], None] | None,
    descriptor: SessionKindPlugin,
    name: str,
) -> None:
    """Run the caller's rollback hook. A failing hook must not abort the rollback."""
    if hook is None:
        return
    try:
        hook(descriptor)
    except Exception as exc:
        log.warning("octowright.plugins.rollback_hook_failed", name=name, error=repr(exc))
