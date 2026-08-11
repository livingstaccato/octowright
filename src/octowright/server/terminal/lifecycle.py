# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""@mcp.tool surface for driving terminal sessions (PTY / SSH / Telnet)."""

from __future__ import annotations

from typing import Any

from octowright.dashboard_events import publish_dashboard_invalidation_nowait
from octowright.server._state import mcp, terminal_pool
from octowright.terminal.connector_config import SSH_DEFAULT_PORT as _DEFAULT_SSH_PORT
from octowright.terminal.connector_config import TELNET_DEFAULT_PORT as _DEFAULT_TELNET_PORT
from octowright.terminal.connector_config import (
    pty_connector_config as _pty_connector_config,
)
from octowright.terminal.connector_config import (
    ssh_connector_config as _ssh_connector_config,
)
from octowright.terminal.connector_config import (
    telnet_connector_config as _telnet_connector_config,
)
from octowright.terminal.errors import (
    ProtectedTerminalCloseError,
    TerminalDisconnectedError,
    TerminalPoolUnavailableError,
)
from octowright.terminal.pool import TerminalPool


def _pool() -> TerminalPool:
    # This module is imported only when _state.terminal_pool is not None
    # (server/__init__ gates it on terminal availability), so this never fires in
    # practice. It's an explicit raise (not assert) so the guard survives
    # `python -O` instead of letting a None pool reach `.launch`.
    pool = terminal_pool
    if pool is None:
        raise TerminalPoolUnavailableError("terminal tools reached without an available terminal_pool")
    return pool


@mcp.tool(
    structured_output=False,
    description=(
        "Launch a terminal session and start recording. kind='pty' runs a local "
        "shell (command=, default /bin/bash); kind='ssh' connects to a remote host "
        "(host/user/key_path/known_hosts); kind='telnet' connects to a telnet BBS or "
        "server (host/port, default port 23) with CP437 decoding and RFC 854 IAC "
        "negotiation. Returns instance_id for the other terminal_* tools."
    ),
)
async def terminal_launch(
    kind: str = "pty",
    command: str | None = None,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    key_path: str | None = None,
    password: str | None = None,
    known_hosts: str | None = None,
    insecure_no_host_check: bool = False,
    cols: int = 80,
    rows: int = 24,
    label: str | None = None,
    profile: str | None = None,
    protected: bool = False,
) -> dict[str, Any]:
    if kind == "ssh":
        cfg = _ssh_connector_config(
            host=host,
            port=port if port is not None else _DEFAULT_SSH_PORT,
            user=user,
            key_path=key_path,
            password=password,
            known_hosts=known_hosts,
            insecure_no_host_check=insecure_no_host_check,
        )
    elif kind == "telnet":
        cfg = _telnet_connector_config(
            host=host,
            port=port if port is not None else _DEFAULT_TELNET_PORT,
        )
    else:
        cfg = _pty_connector_config(command=command, cols=cols, rows=rows)
    try:
        result = await _pool().launch(
            kind=kind, connector_config=cfg, label=label, profile=profile, protected=protected
        )
    except ValueError as exc:
        # The SSH connector raises ValueError synchronously (missing known_hosts,
        # unknown config key) inside build_connector; surface it as a clean tool error.
        return {"ok": False, "error": str(exc)}
    publish_dashboard_invalidation_nowait("sessions")
    return result


@mcp.tool(structured_output=False, description="Send input text (e.g. a command + '\\n') to a terminal session.")
async def terminal_send_input(instance_id: str, text: str, password: bool = False) -> dict[str, Any]:
    session = _pool().get(instance_id)
    try:
        await session.engine.send_input(text, password=password)
    except TerminalDisconnectedError as exc:
        # The connector is gone — the bytes were NOT delivered. Report failure
        # instead of a misleading {"ok": true}.
        return {"ok": False, "error": str(exc)}
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
