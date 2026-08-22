# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The live registry of enabled session kinds.

Core keeps no parallel session table: a plugin's ``SessionPool`` is the single
registry for its kind, and this object is the map from kind to pool plus the
status ledger for every plugin core knows about, whatever its state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from provide.telemetry import get_logger

from octowright.plugins.contract import SessionKindPlugin, SessionPool, SessionRecord, capabilities_of
from octowright.plugins.discovery import DiscoveredPlugin

log = get_logger("octowright.plugins.registry")


@dataclass(frozen=True)
class LoadedPlugin:
    """An enabled plugin and everything core built from it."""

    descriptor: SessionKindPlugin
    pool: SessionPool
    adapter: Any
    capabilities: frozenset[str]
    discovered: DiscoveredPlugin | None


@dataclass
class PluginRegistry:
    """Kind → loaded plugin, plus a status row for every plugin core saw."""

    _loaded: dict[str, LoadedPlugin] = field(default_factory=dict)
    _states: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register(
        self,
        descriptor: SessionKindPlugin,
        *,
        pool: SessionPool,
        adapter: Any,
        discovered: DiscoveredPlugin | None,
    ) -> LoadedPlugin:
        loaded = LoadedPlugin(
            descriptor=descriptor,
            pool=pool,
            adapter=adapter,
            capabilities=capabilities_of(adapter) if adapter is not None else frozenset(),
            discovered=discovered,
        )
        self._loaded[descriptor.kind] = loaded
        row: dict[str, Any] = dict(discovered.status_row("enabled")) if discovered else {"state": "enabled"}
        row.update(
            {
                "name": discovered.name if discovered else descriptor.kind,
                "kind": descriptor.kind,
                "display_name": descriptor.display_name,
                "plugin_api_version": descriptor.plugin_api_version,
                "tool_names": sorted(descriptor.tool_names),
                "capabilities": sorted(loaded.capabilities),
            }
        )
        self._states[row["name"]] = row
        return loaded

    def record_failure(
        self,
        *,
        name: str,
        reason: str,
        discovered: DiscoveredPlugin | None,
        descriptor: SessionKindPlugin | None = None,
    ) -> None:
        """Record a failed load.

        Descriptor fields are optional here on purpose: a plugin that raised
        while importing its own module has no descriptor to report, and that
        is the earliest and most common failure.
        """
        row: dict[str, Any] = dict(discovered.status_row("failed")) if discovered else {"name": name, "state": "failed"}
        row["name"] = name
        row["state"] = "failed"
        row["reason"] = reason
        if descriptor is not None:
            row["kind"] = descriptor.kind
            row["display_name"] = descriptor.display_name
            row["plugin_api_version"] = descriptor.plugin_api_version
            row["tool_names"] = sorted(descriptor.tool_names)
        self._states[name] = row

    def record_state(self, *, name: str, state: str, discovered: DiscoveredPlugin | None = None) -> None:
        """Record a non-loading state — ``disabled`` or ``missing``."""
        row: dict[str, Any] = dict(discovered.status_row(state)) if discovered else {"name": name, "state": state}
        self._states[name] = row

    def kinds(self) -> list[str]:
        return sorted(self._loaded)

    def pools(self) -> dict[str, SessionPool]:
        return {kind: loaded.pool for kind, loaded in self._loaded.items()}

    def get_plugin(self, kind: str) -> LoadedPlugin:
        return self._loaded[kind]

    def maybe_get(self, instance_id: str) -> SessionRecord | None:
        """Resolve a session by id across every registered pool."""
        for loaded in self._loaded.values():
            found = loaded.pool.maybe_get(instance_id)
            if found is not None:
                return found
        return None

    def id_in_use(self, instance_id: str) -> bool:
        return self.maybe_get(instance_id) is not None

    def status_rows(self) -> list[dict[str, Any]]:
        return [self._states[name] for name in sorted(self._states)]
