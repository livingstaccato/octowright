# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pin the loader's narrow coupling to the MCP SDK's private tool mapping.

``loader._tool_names`` / ``_remove_tools`` reach into ``ToolManager._tools``
because rollback has to remove what was *actually* registered, not what was
declared. That coupling is accepted and deliberate — but if the SDK renames the
attribute, an empty snapshot would make every delta empty and every rollback a
no-op, leaving exactly the half-registered plugin the machinery exists to
prevent. This test makes such a rename fail loudly here instead.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer

from octowright.plugins.loader import _tool_names


def test_a_real_mcp_server_exposes_the_private_tool_mapping():
    server = MCPServer("octowright-coupling-probe")

    assert hasattr(server, "_tool_manager"), "MCPServer no longer exposes _tool_manager; update plugins/loader.py"
    assert isinstance(server._tool_manager._tools, dict), (
        "ToolManager._tools is no longer a dict; plugin tool rollback reads and mutates it"
    )


def test_the_snapshot_tracks_a_real_registration():
    server = MCPServer("octowright-coupling-probe")
    before = _tool_names(server._tool_manager)

    @server.tool()
    async def probe_tool() -> str:
        """A tool registered only to prove the snapshot sees it."""
        return "ok"

    assert _tool_names(server._tool_manager) - before == {"probe_tool"}


def test_the_snapshot_raises_rather_than_reporting_an_empty_delta():
    # A silently-empty snapshot is the failure this guard exists to prevent.
    class _RenamedToolManager:
        def __init__(self) -> None:
            self._registry: dict[str, object] = {}

    with pytest.raises(AttributeError):
        _tool_names(_RenamedToolManager())
