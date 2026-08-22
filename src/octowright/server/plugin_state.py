# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Process-global plugin registry accessor for plugin tool modules.

A plugin's ``@mcp.tool`` functions need their pool at call time, but they are
imported *before* ``create_pool`` runs (the loader registers tools first so a
tool failure never has to tear a pool down). So they look the pool up through
this seam instead of closing over it.
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
