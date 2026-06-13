# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""@mcp.tool surface for driving terminal sessions (PTY / SSH)."""

from __future__ import annotations

from typing import Any

from octowright.dashboard_events import publish_dashboard_invalidation_nowait
from octowright.server._state import mcp, terminal_pool
from octowright.terminal.errors import ProtectedTerminalCloseError
from octowright.terminal.pool import TerminalPool


def _pool() -> TerminalPool:
    # This module is imported only when _state.terminal_pool is not None
    # (server/__init__ gates it on terminal availability), so this never raises.
    pool = terminal_pool
    assert pool is not None, "terminal tools imported without an available terminal_pool"
    return pool


@mcp.tool(
    structured_output=False,
    description=(
        "Launch a terminal session and start recording. kind='pty' runs a local "
        "shell (command=, default /bin/bash); kind='ssh' connects to a remote host "
        "(host/user/key_path/known_hosts). Returns instance_id for the other terminal_* tools."
    ),
)
async def terminal_launch(
    kind: str = "pty",
    command: str | None = None,
    cols: int = 80,
    rows: int = 24,
    label: str | None = None,
    profile: str | None = None,
    protected: bool = False,
) -> dict[str, Any]:
    if command is None:
        command = "/bin/bash"
    cfg: dict[str, Any] = {"cols": cols, "rows": rows, "command": command}
    result = await _pool().launch(kind=kind, connector_config=cfg, label=label, profile=profile, protected=protected)
    publish_dashboard_invalidation_nowait("sessions")
    return result


@mcp.tool(structured_output=False, description="Send input text (e.g. a command + '\\n') to a terminal session.")
async def terminal_send_input(instance_id: str, text: str, password: bool = False) -> dict[str, Any]:
    session = _pool().get(instance_id)
    await session.engine.send_input(text, password=password)
    return {"ok": True, "event_count": session.recorder.event_count}


@mcp.tool(structured_output=False, description="Return the current screen text + cursor of a terminal session.")
async def terminal_snapshot(instance_id: str) -> dict[str, Any]:
    return await _pool().get(instance_id).engine.snapshot()


@mcp.tool(
    structured_output=False,
    description="Return the current screen text of a terminal session.",
)
async def terminal_read(instance_id: str) -> dict[str, Any]:
    snap = await _pool().get(instance_id).engine.snapshot()
    return {"screen": snap["screen"]}


@mcp.tool(
    structured_output=False,
    description="Wait until a regex (prompt=) or substring (text=) appears on the terminal screen, or timeout.",
)
async def terminal_wait_for(
    instance_id: str, prompt: str | None = None, text: str | None = None, timeout: float = 10.0
) -> dict[str, Any]:
    matched = await _pool().get(instance_id).engine.wait_for(prompt=prompt, text=text, timeout=timeout)
    snap = await _pool().get(instance_id).engine.snapshot()
    return {"matched": matched, "screen": snap["screen"]}


@mcp.tool(
    structured_output=False,
    description="Close a terminal session. Refuses a protected session unless force=True.",
)
async def terminal_close(instance_id: str, force: bool = False) -> dict[str, Any]:
    try:
        await _pool().close(instance_id, force=force)
    except ProtectedTerminalCloseError as exc:
        return {"closed": False, "reason": str(exc)}
    publish_dashboard_invalidation_nowait("sessions")
    return {"closed": True}


@mcp.tool(structured_output=False, description="List live terminal sessions.")
async def terminal_list() -> list[dict[str, Any]]:
    return _pool().list_sessions()
