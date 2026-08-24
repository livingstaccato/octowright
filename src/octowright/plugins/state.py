# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Process-global plugin registry.

Lives here rather than under ``octowright.server`` because of what importing
that package costs: ``server/__init__`` imports every tool submodule to trigger
``@mcp.tool`` registration, so reaching the registry through it pulled the whole
~129-tool surface -- Playwright included -- into any caller. Measured: validating
a single scenario participant took ``sys.modules`` from 392 to 1146.

That mattered because the registry has readers well below the tool layer.
``scenario_kinds`` resolves a participant's adapter during ``_validate_participant_kind``,
which is core model code with no business loading a browser driver.

``octowright.server.plugin_state`` re-exports these functions, so the tool
modules that reach for it there keep working. It re-exports the *functions*
rather than the value, so both paths mutate and read the one global here --
there is no second source of truth.
"""

from __future__ import annotations

from octowright.plugins.contract import SessionPool
from octowright.plugins.registry import PluginRegistry

_registry = PluginRegistry()


def registry() -> PluginRegistry:
    return _registry


def set_registry(value: PluginRegistry) -> None:
    """Replace the process-global registry. Used at daemon start and by tests."""
    global _registry  # one process-global, same seam as http/state
    _registry = value


def pool_for(kind: str) -> SessionPool:
    return _registry.get_plugin(kind).pool
