# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pure builders for uterm connector_config dicts (no uterm import).

Lives in the terminal package but depends only on ``octowright.defaults`` +
stdlib, so core callers (e.g. ``scenarios.py``) can build terminal launch
configs without pulling the optional uterm dependency.
"""

from __future__ import annotations

from typing import Any

from octowright.defaults import SSH_DEFAULT_PORT

__all__ = ["SSH_DEFAULT_PORT", "pty_connector_config", "ssh_connector_config"]


def pty_connector_config(*, command: str | None, cols: int | None, rows: int | None) -> dict[str, Any]:
    """Build the PTY connector_config, applying the same defaults as terminal_launch."""
    return {"command": command or "/bin/bash", "cols": cols or 80, "rows": rows or 24}


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
        cfg["client_key_path"] = key_path
    if password is not None:
        cfg["password"] = password
    if known_hosts is not None:
        cfg["known_hosts"] = known_hosts
    if insecure_no_host_check:
        cfg["insecure_no_host_check"] = True
    return cfg
