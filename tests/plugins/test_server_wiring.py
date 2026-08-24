# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.plugins.registry import PluginRegistry
from octowright.server import plugin_state


def test_status_reports_the_plugins_block():
    # octowright_status is sync — see tests/test_leader_mode.py, which calls it
    # without await.
    from octowright.server.meta import octowright_status

    payload = octowright_status()
    assert "plugins" in payload
    assert isinstance(payload["plugins"], list)


def test_plugin_state_registry_is_replaceable():
    original = plugin_state.registry()
    replacement = PluginRegistry()
    try:
        plugin_state.set_registry(replacement)
        assert plugin_state.registry() is replacement
    finally:
        plugin_state.set_registry(original)


def test_state_exposes_the_process_registry():
    from octowright.server import _state

    assert isinstance(_state.plugin_registry, PluginRegistry)
    assert isinstance(_state.resolved_plugins, list)


def test_plugin_activation_is_imported_after_core_tool_modules():
    # Load-bearing ordering: the SDK's add_tool is first-wins, so a plugin
    # activated before core registration could shadow a core tool.
    from pathlib import Path

    import octowright.server as server_pkg

    source = Path(server_pkg.__file__).read_text()
    assert source.index("_plugin_activation") > source.index("import web as _web")


def test_no_plugins_enabled_by_default():
    from octowright.server import _state

    # A core install enables nothing, so the tool surface is unchanged.
    assert _state.plugin_registry.kinds() == []
