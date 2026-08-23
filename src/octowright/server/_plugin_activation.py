# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Import each enabled plugin's tool module and build its pool.

Imported **last** by ``server/__init__``, after every core tool submodule has
registered. That ordering is load-bearing rather than tidy: the MCP SDK's
``add_tool`` is first-wins, so activating before core registration would let a
plugin claiming a non-reserved kind (``macro_run``, ``capture_create``,
``persona_get``) shadow core's tool — the inversion of the collision check's
whole purpose. ``_optional_tools`` is imported early for the terminal extra and
is deliberately not used for this.
"""

from __future__ import annotations

from octowright import defaults
from octowright.plugins import loader as plugin_loader
from octowright.plugins.session_launch import PluginContext
from octowright.server._state import mcp, plugin_registry, resolved_plugins


def _ctx(kind: str) -> PluginContext:
    return PluginContext(
        kind=kind,
        recordings_dir=defaults.RECORDINGS_DIR,
        id_in_use=plugin_registry.id_in_use,
    )


plugin_loader.activate(
    registry=plugin_registry,
    resolved=resolved_plugins,
    ctx_factory=_ctx,
    tool_manager=mcp._tool_manager,
)
