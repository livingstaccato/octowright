# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""@mcp.tool surface for driving terminal sessions (PTY / SSH)."""

from __future__ import annotations

from typing import Any

from provide.uterm.defaults import TerminalDefaults

from octowright.dashboard_events import publish_dashboard_invalidation_nowait
from octowright.server._state import mcp, terminal_pool
from octowright.terminal.errors import ProtectedTerminalCloseError
from octowright.terminal.pool import TerminalPool

# Standard SSH port; sourced from uterm's single default rather than inlined so
# the tool default tracks the connector's own default.
_DEFAULT_SSH_PORT = TerminalDefaults.SSH_REMOTE_PORT


def _pool() -> TerminalPool:
    # This module is imported only when _state.terminal_pool is not None
    # (server/__init__ gates it on terminal availability), so this never raises.
    pool = terminal_pool
    assert pool is not None, "terminal tools imported without an available terminal_pool"
    return pool


def _ssh_connector_config(
    *,
    host: str | None,
    port: int,
    user: str | None,
    key_path: str | None,
    password: str | None,
    known_hosts: str | None,
    insecure_no_host_check: bool,
) -> dict[str, Any]:
    """Map ``terminal_launch`` SSH args to the uterm SSH connector's config keys.

    The uterm ``SshSessionConnector`` validates its config against a fixed
    allow-list and raises ``ValueError`` on any unknown key, so this emits ONLY
    connector-recognized keys: no PTY ``command`` and no ``cols``/``rows`` (the
    SSH connector fixes the remote PTY size itself). Omitted args are dropped so
    the connector falls back to its own defaults rather than seeing ``None``.
    """
    cfg: dict[str, Any] = {"port": port}
    if host is not None:
        cfg["host"] = host
    if user is not None:
        cfg["username"] = user
    if key_path is not None:
        cfg["client_key_path"] = key_path
    if password is not None:
        cfg["password"] = password
    if known_hosts is not None:
        cfg["known_hosts"] = known_hosts
    if insecure_no_host_check:
        cfg["insecure_no_host_check"] = True
    return cfg


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
    host: str | None = None,
    port: int = _DEFAULT_SSH_PORT,
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
            port=port,
            user=user,
            key_path=key_path,
            password=password,
            known_hosts=known_hosts,
            insecure_no_host_check=insecure_no_host_check,
        )
    else:
        if command is None:
            command = "/bin/bash"
        cfg = {"cols": cols, "rows": rows, "command": command}
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
