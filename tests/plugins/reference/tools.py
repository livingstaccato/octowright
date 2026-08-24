# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP tools the reference plugin registers on import.

Registration is an import-time side effect, exactly as core's own tool
modules do it — the loader snapshots the tool manager around this import so a
partial registration can be rolled back.
"""

from __future__ import annotations

from typing import Any

from octowright.server import plugin_state
from octowright.server._state import mcp


@mcp.tool()
async def refkind_launch(label: str | None = None, protected: bool = False) -> dict[str, Any]:
    """Launch a reference session."""
    pool = plugin_state.pool_for("refkind")
    return dict(await pool.launch(label=label, protected=protected))


@mcp.tool()
async def refkind_close(instance_id: str, force: bool = False) -> dict[str, Any]:
    """Close a reference session."""
    pool = plugin_state.pool_for("refkind")
    return dict(await pool.close(instance_id, force=force))
