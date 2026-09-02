# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pure builders for uterm connector_config dicts (no uterm import).

Depends only on stdlib, so callers elsewhere in this package can build
terminal launch configs without pulling the optional uterm dependency.
``SSH_DEFAULT_PORT`` used to live in core's ``octowright.defaults`` (moved
here in step 5's deletion phase, alongside ``SUPPORTED_TERMINAL_KINDS`` below)
so that core no longer carries terminal-specific constants -- this package
owns its own defaults entirely.
"""

from __future__ import annotations

import os
from typing import Any

#: Default SSH port for terminal SSH connectors (scenario participants /
#: terminal_launch). Overridable so a deployment behind a jump host or a
#: nonstandard sshd can set it once instead of passing port= everywhere.
SSH_DEFAULT_PORT: int = int(os.environ.get("OCTOWRIGHT_SSH_PORT", "22"))

#: Connector types for a terminal scenario participant (its kind is "terminal").
SUPPORTED_TERMINAL_KINDS = ("pty", "ssh")

TELNET_DEFAULT_PORT = 23

# canonical order: network ssh, telnet then local pty (octowright: no ws connector)
__all__ = [  # noqa: RUF022
    "SSH_DEFAULT_PORT",
    "SUPPORTED_TERMINAL_KINDS",
    "TELNET_DEFAULT_PORT",
    "ssh_connector_config",
    "telnet_connector_config",
    "pty_connector_config",
]


def ssh_connector_config(
    *,
    host: str | None,
    port: int,
    user: str | None,
    key_path: str | None,
    password: str | None,
    known_hosts: str | None,
    insecure_no_host_check: bool,
) -> dict[str, Any]:
    """Map SSH args to the uterm SSH connector's allow-listed config keys.

    The connector rejects unknown keys and fixes its own remote PTY size, so this
    emits no ``cols``/``rows``/``command``. Omitted args are dropped so the
    connector falls back to its own defaults rather than seeing ``None``.
    """
    cfg: dict[str, Any] = {"port": port}
    if host is not None:
        cfg["host"] = host
    if user is not None:
        cfg["username"] = user
    if key_path is not None:
        # ``client_key``, NOT ``client_key_path``. Both sit in the connector's
        # allow-list, but it *raises* on ``client_key_path`` (and so does the
        # egress chokepoint), so emitting it made every keyed SSH launch fail
        # with "client_key_path is not supported". ``client_key`` is appended
        # as a plain string and asyncssh resolves a string as a file path.
        cfg["client_key"] = key_path
    if password is not None:
        cfg["password"] = password
    if known_hosts is not None:
        cfg["known_hosts"] = known_hosts
    if insecure_no_host_check:
        cfg["insecure_no_host_check"] = True
    return cfg


def telnet_connector_config(*, host: str | None, port: int) -> dict[str, Any]:
    """Build the telnet connector_config for TelnetSessionConnector.

    Only ``host`` and ``port`` are supported (``input_mode`` defaults to
    ``"open"`` inside the connector and is never surfaced to MCP callers).
    ``hub_overlay=False`` disables the uterm hub status header and preserves
    raw CRLF line endings so xterm.js renders the CP437 stream correctly.
    No ``cols``/``rows``/``command`` — the connector hardcodes its own terminal
    geometry (80x25) to match typical BBS server expectations.
    """
    cfg: dict[str, Any] = {"port": port, "hub_overlay": False}
    if host is not None:
        cfg["host"] = host
    return cfg


def pty_connector_config(*, command: str | None, cols: int | None, rows: int | None) -> dict[str, Any]:
    """Build the PTY connector_config, applying the same defaults as terminal_launch."""
    return {"command": command or "/bin/bash", "cols": cols or 80, "rows": rows or 24}
