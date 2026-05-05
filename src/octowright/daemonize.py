# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Spawn a detached background leader so the leader survives parent close.

Why this exists: an MCP client like Claude Code launches ``octowright serve``
as a stdio child. When the client closes, it sends SIGTERM and (after a
brief grace) SIGKILL. SIGTERM we can catch (see ``cli.serve``); SIGKILL we
can't. Browsers die with the leader.

The fix: when a ``serve`` invocation decides it should become the leader,
it instead **forks a fully-detached background process** that becomes the
real leader. The original Claude-Code-launched process becomes a follower
bridging to the daemon. Closing Claude Code kills the bridge but the
daemon — running in its own session, with stdin/out/err pointed at
``/dev/null`` — is unaffected.

The daemon is invoked with ``--daemon-mode``, which tells it to skip the
lock check (it knows it's the leader) and arm the idle watchdog
immediately (so an unused daemon exits after the grace period).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from typing import IO

import octowright.singleton as _sn
from octowright.config_paths import user_config_dir

# Daemon stderr goes here so we have something to investigate when the daemon
# misbehaves. Rotated by file size on each spawn (truncated above 1 MB) to
# avoid unbounded growth on dev machines.
_DAEMON_LOG = user_config_dir() / "octowright-daemon.log"
_DAEMON_LOG_MAX_BYTES = 1_000_000


def _open_daemon_log() -> IO[bytes]:
    """Open the daemon-stderr log file, truncating if it's grown too large."""
    _DAEMON_LOG.parent.mkdir(parents=True, exist_ok=True)
    if _DAEMON_LOG.exists() and _DAEMON_LOG.stat().st_size > _DAEMON_LOG_MAX_BYTES:
        _DAEMON_LOG.unlink()
    return _DAEMON_LOG.open("ab")


def spawn_daemon(
    *,
    http_host: str | None,
    http_port: int | None,
    idle_grace: float | None,
) -> int:
    """Spawn a fully detached background ``octowright serve --daemon-mode`` process.

    Returns the spawned PID. The process is in a new session with no
    controlling terminal and with stdin/stdout pointed at /dev/null, so
    nothing about the parent's lifecycle can reach it.
    """
    args: list[str] = [sys.argv[0], "serve", "--daemon-mode"]
    if http_host:
        args.extend(["--http-host", http_host])
    if http_port is not None:
        args.extend(["--http-port", str(http_port)])
    if idle_grace is not None:
        args.extend(["--idle-grace", str(idle_grace)])

    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=_open_daemon_log(),
        start_new_session=True,
        close_fds=True,
        env=os.environ.copy(),
    )
    return proc.pid


async def wait_for_daemon(timeout: float = 10.0, poll_seconds: float = 0.2) -> _sn.LeaderInfo | None:
    """Poll the lockfile + HTTP probe until the daemon is ready, or give up.

    Returns the leader info on success, or None if the daemon failed to come
    up within ``timeout`` seconds. The caller can then fall back to running
    the leader inline (the legacy non-daemonized path).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_seconds)
        info = _sn.read_lock()
        if info is None or _sn.is_stale(info):
            continue
        if await _sn.probe_http_alive(info):
            return info
    return None
