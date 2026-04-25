# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Singleton-leader detection for ``octowright serve``.

Each MCP client (Claude Code, Cursor, etc.) spawns its own ``octowright serve``
stdio process. Rather than each one running its own browser pool, the first
instance becomes the **leader**: it writes a lockfile at
``~/.config/undef/octowright.lock`` describing its PID and HTTP-MCP endpoint,
and serves both stdio MCP and HTTP MCP. Subsequent instances become
**followers**: they read the lockfile and bridge stdin/stdout to the leader's
HTTP MCP endpoint instead of spawning their own pool.

The lockfile is purely advisory — there is no flock(). We rely on PID liveness
and a short HTTP probe to detect a stale lock. Concurrent acquisition by two
processes at the exact same moment is possible but rare and self-corrects on
the next boot (the probe will reveal one of them as stale).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

LOCK_PATH = Path.home() / ".config" / "undef" / "octowright.lock"


@dataclass
class LeaderInfo:
    """Snapshot of the running leader as recorded in the lockfile."""

    pid: int
    http_host: str
    http_port: int
    mcp_url: str
    started_at: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> LeaderInfo:
        data = json.loads(s)
        return cls(**data)


def read_lock(path: Path = LOCK_PATH) -> LeaderInfo | None:
    """Return the parsed lockfile, or None if it doesn't exist or is corrupt."""
    if not path.exists():
        return None
    try:
        return LeaderInfo.from_json(path.read_text())
    except (json.JSONDecodeError, TypeError, KeyError):
        # Corrupt lockfile — treat as if no leader; the caller will overwrite it.
        return None


def write_lock(info: LeaderInfo, path: Path = LOCK_PATH) -> None:
    """Atomically replace the lockfile with ``info``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(info.to_json())
    tmp.replace(path)


def remove_lock(path: Path = LOCK_PATH) -> None:
    """Delete the lockfile if present. Safe to call multiple times."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def pid_is_alive(pid: int) -> bool:
    """True if the OS still has a process with this PID."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by someone else — still "alive" for
        # the purposes of "should I take over the lock".
        return True
    return True


def is_stale(info: LeaderInfo) -> bool:
    """A lock is stale when its recorded PID is no longer running.

    Liveness of the HTTP endpoint is checked separately by the caller — that
    requires an event loop, so we keep this function synchronous.
    """
    return not pid_is_alive(info.pid)


def make_leader_info(http_host: str, http_port: int) -> LeaderInfo:
    """Build the lockfile record for ``this`` process becoming leader."""
    return LeaderInfo(
        pid=os.getpid(),
        http_host=http_host,
        http_port=http_port,
        # Trailing slash matters: Starlette's Mount strips ``/mcp`` and routes
        # the remainder. The streamable-http app's inner route is ``/``, so the
        # client must POST to ``/mcp/`` (a bare ``/mcp`` returns 405).
        mcp_url=f"http://{http_host}:{http_port}/mcp/",
        started_at=time.time(),
    )
