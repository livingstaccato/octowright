# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The reference plugin's tool seam, exercised against the REAL ``mcp``.

Every other plugin test drives a fake tool manager, so nothing proved that a
plugin's ``tool_module`` import registers real tools on the real server, that
the measured delta matches the declaration, or that ``plugin_state.pool_for``
resolves at call time. ``tests/plugins/reference/tools.py`` had no consumer at
all — running ``tests/plugins`` left it absent from ``sys.modules``.

This mutates process-global state (the tool manager's mapping, the plugin
registry, the profile filter, the registered plugin profiles), so the autouse
fixture restores every one of them.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from octowright.plugins.discovery import ENTRY_POINT_GROUP, discover
from octowright.plugins.loader import activate, resolve_descriptors
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from octowright.server import plugin_state, profiles
from octowright.server._state import mcp
from tests.plugins.reference.plugin import plugin

TOOL_MODULE = "tests.plugins.reference.tools"


@pytest.fixture(autouse=True)
def _clean_activation():
    """Undo everything an activation writes into process-global state."""
    tools_before = set(mcp._tool_manager._tools)
    previous_registry = plugin_state.registry()
    previous_allowed = mcp._allowed_tools
    profiles.reset_plugin_profiles()
    sys.modules.pop(TOOL_MODULE, None)
    try:
        yield
    finally:
        for name in set(mcp._tool_manager._tools) - tools_before:
            mcp._tool_manager._tools.pop(name, None)
        mcp._allowed_tools = previous_allowed
        plugin_state.set_registry(previous_registry)
        profiles.reset_plugin_profiles()
        sys.modules.pop(TOOL_MODULE, None)


def _ctx_factory(registry: PluginRegistry, recordings_dir: Path) -> Callable[[str], PluginContext]:
    def _build(kind: str) -> PluginContext:
        return PluginContext(kind=kind, recordings_dir=recordings_dir, id_in_use=registry.id_in_use)

    return _build


def _resolve_the_real_reference_plugin(registry: PluginRegistry):
    discovered = discover(
        entry_points=[
            EntryPoint(
                name="refkind",
                value="tests.plugins.reference.plugin:plugin",
                group=ENTRY_POINT_GROUP,
            )
        ]
    )
    return resolve_descriptors(
        registry=registry,
        discovered=discovered,
        enabled=["refkind"],
        tool_manager=mcp._tool_manager,
    )


def test_reference_plugin_activates_end_to_end(tmp_path, caplog):
    registry = PluginRegistry()
    plugin_state.set_registry(registry)

    resolved = _resolve_the_real_reference_plugin(registry)
    assert [item.descriptor for item in resolved] == [plugin]

    # §6.5's bootstrap order: register the plugin's profile, THEN narrow the
    # filter to it. The filter is read at @mcp.tool decoration time, so a
    # profile registered afterwards would be too late.
    profiles.register_plugin_profile(plugin.profile_name, plugin.tool_names)
    caplog.clear()
    with caplog.at_level("WARNING"):
        mcp._allowed_tools = profiles.build_allowed_set(plugin.profile_name)
    messages = [record.getMessage() for record in caplog.records]
    assert not any("profile.unknown" in message for message in messages)
    assert not any("profile.all_unknown" in message for message in messages)

    activate(
        registry=registry,
        resolved=resolved,
        ctx_factory=_ctx_factory(registry, tmp_path),
        tool_manager=mcp._tool_manager,
    )

    # The tool module really was imported, and really did register — under a
    # narrow OCTOWRIGHT_PROFILE naming only this plugin's profile.
    assert TOOL_MODULE in sys.modules
    assert {"refkind_launch", "refkind_close"} <= set(mcp._tool_manager._tools)

    # A plugin tool resolves its pool through plugin_state at call time.
    assert plugin_state.pool_for("refkind") is registry.get_plugin("refkind").pool

    rows = {row["name"]: row for row in registry.status_rows()}
    assert rows["refkind"]["state"] == "enabled"
    assert rows["refkind"]["kind"] == "refkind"
    assert sorted(rows["refkind"]["tool_names"]) == ["refkind_close", "refkind_launch"]


@pytest.mark.asyncio
async def test_the_registered_tools_drive_the_activated_pool(tmp_path):
    registry = PluginRegistry()
    plugin_state.set_registry(registry)

    resolved = _resolve_the_real_reference_plugin(registry)
    profiles.register_plugin_profile(plugin.profile_name, plugin.tool_names)
    activate(
        registry=registry,
        resolved=resolved,
        ctx_factory=_ctx_factory(registry, tmp_path),
        tool_manager=mcp._tool_manager,
    )

    from tests.plugins.reference import tools as reference_tools

    launched = await reference_tools.refkind_launch(label="demo")
    assert launched["kind"] == "refkind"
    assert registry.get_plugin("refkind").pool.maybe_get(launched["instance_id"]) is not None

    closed = await reference_tools.refkind_close(launched["instance_id"])
    assert closed["closed"] is True
