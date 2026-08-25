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


def test_terminals_profile_registered() -> None:
    from octowright.server.profiles import PROFILES

    assert "terminal_launch" in PROFILES["terminals"]
    assert "terminal_close" in PROFILES["terminals"]


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
