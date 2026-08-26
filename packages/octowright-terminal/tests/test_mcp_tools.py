# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys

import pytest
from octowright_terminal.plugin import TOOL_NAMES

from octowright.plugins.registry import PluginRegistry
from octowright.server import plugin_state

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")

# The autouse `_activated_terminal_plugin` fixture in conftest.py registers a
# working "terminal" pool into plugin_state before every test in this suite —
# see its docstring for why the tools need that.


async def test_terminal_tool_lifecycle() -> None:
    from octowright_terminal import tools

    launched = await tools.terminal_launch(kind="pty", command="/bin/cat", label="t")
    iid = launched["instance_id"]
    try:
        assert launched["kind"] == "terminal"
        listed = await tools.terminal_list()
        assert any(s["instance_id"] == iid for s in listed)

        await tools.terminal_send_input(instance_id=iid, text="hi-tools\n")
        waited = await tools.terminal_wait_for(instance_id=iid, text="hi-tools", timeout=5.0)
        assert waited["matched"] is True
        snap = await tools.terminal_snapshot(instance_id=iid)
        assert "hi-tools" in snap["screen"]
    finally:
        closed = await tools.terminal_close(instance_id=iid)
        assert closed["closed"] is True


async def test_terminal_close_refuses_protected_without_force() -> None:
    from octowright_terminal import tools

    launched = await tools.terminal_launch(kind="pty", command="/bin/cat", protected=True)
    iid = launched["instance_id"]
    try:
        result = await tools.terminal_close(instance_id=iid)
        assert result["closed"] is False
        assert "protected" in result["reason"]
    finally:
        await tools.terminal_close(instance_id=iid, force=True)


def test_the_terminals_profile_is_the_plugin_s_not_core_s() -> None:
    """Core must NOT reserve the name, or the plugin cannot load at all.

    ``register_plugin_profile`` refuses any name already present in core's
    static ``PROFILES``, so while core carried a ``"terminals"`` entry the
    plugin failed activation with a profile collision and its kind never
    registered -- verified against the real activation path, not inferred.
    The profile itself is unchanged; it is simply declared by the descriptor
    now and registered when the plugin loads.
    """
    from octowright_terminal.plugin import plugin

    from octowright.server.profiles import PROFILES, register_plugin_profile, unregister_plugin_profile

    assert "terminals" not in PROFILES, "core reserving this name makes the plugin unloadable"
    assert plugin.profile_name == "terminals"
    assert {"terminal_launch", "terminal_close"} <= set(plugin.tool_names)

    # And the name is actually registrable, which is the property that broke.
    register_plugin_profile(plugin.profile_name, plugin.tool_names)
    unregister_plugin_profile(plugin.profile_name)


def test_pool_raises_clean_error_when_terminal_pool_missing() -> None:
    # Defensive guard: if a tool is ever reached with no "terminal" plugin
    # registered (an operator has the extra installed but did not name it in
    # OCTOWRIGHT_PLUGINS, or a call races activation), it must fail loudly with
    # a typed error -- not a bare KeyError leaking out of plugin_state.pool_for.
    from octowright_terminal import tools
    from octowright_terminal.errors import TerminalPoolUnavailableError

    plugin_state.set_registry(PluginRegistry())  # no "terminal" entry
    with pytest.raises(TerminalPoolUnavailableError):
        tools._pool()


def test_declared_tool_names_match_what_the_module_registers():
    """A declared name core validates against, and a registered name that does
    not match it, is a collision check that checks the wrong target."""
    import octowright_terminal.tools  # noqa: F401  (import registers them)

    from octowright.server._state import mcp

    registered = {name for name in mcp._tool_manager._tools if name.startswith("terminal_")}
    assert registered == set(TOOL_NAMES)


async def test_terminal_launch_reports_the_connector_type_it_opened() -> None:
    """An agent must be able to read back WHICH connector opened.

    Core builds the ``LaunchResult``, so this rides out in the contract's
    ``extra`` map and is flattened here to the top level -- the shape a caller
    reads. It regressed silently once the pool stopped assembling its own
    result dict: ``terminal_launch`` returned only core's five fields and
    ``result["connector_type"]`` became ``None`` with no error.
    """
    from octowright_terminal import tools

    launched = await tools.terminal_launch(kind="pty", command="/bin/cat", label="t")
    iid = launched["instance_id"]
    try:
        assert launched["connector_type"] == "pty"
    finally:
        await tools.terminal_close(instance_id=iid, force=True)


async def test_terminal_launch_cannot_let_extra_overwrite_a_core_field() -> None:
    """Flattening is plugin-owned; the identity fields are not.

    ``extra`` is free-form plugin data, so a merge that let it win would give a
    plugin a way to rewrite the ``instance_id`` every later tool call resolves
    by.
    """
    from octowright_terminal import tools

    from octowright.server import plugin_state

    pool = plugin_state.registry().pools()["terminal"]
    real_launch = pool.launch

    async def _poisoned(**kwargs):
        result = await real_launch(**kwargs)
        result["extra"] = {"instance_id": "hijacked", "connector_type": "pty"}
        return result

    pool.launch = _poisoned  # type: ignore[method-assign]
    try:
        launched = await tools.terminal_launch(kind="pty", command="/bin/cat")
    finally:
        pool.launch = real_launch  # type: ignore[method-assign]
    iid = launched["instance_id"]
    try:
        assert iid != "hijacked"
        assert pool.maybe_get(iid) is not None
    finally:
        await tools.terminal_close(instance_id=iid, force=True)
