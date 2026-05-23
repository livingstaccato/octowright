# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Singleton-leader detection for ``octowright serve``.

Each MCP client (Claude Code, Cursor, etc.) spawns its own ``octowright serve``
stdio process. Rather than each one running its own browser pool, the first
instance becomes the **leader**: it writes a lockfile at
Octowright's user config directory describing its PID and HTTP-MCP endpoint,
and serves both stdio MCP and HTTP MCP. Subsequent instances become
**followers**: they read the lockfile and bridge stdin/stdout to the leader's
HTTP MCP endpoint instead of spawning their own pool.

The leader-election decision (read-probe-then-maybe-spawn) is serialised
across processes by ``election_lock``, an advisory ``fcntl.flock`` on a
sibling lockfile. Without it, two simultaneous starters could both observe
"no live leader" and both spawn a daemon; the second daemon would silently
bind a different port and leave followers bridging to the abandoned one.
On Windows (no fcntl) the lock is a no-op and the original race remains —
self-corrects on the next boot via the PID + HTTP probe.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from octowright import defaults

# LOCK_PATH lives in defaults.py — single source of truth for env-driven
# config. Re-exported here so tests that reload(singleton) (or that
# monkeypatch.setattr the singleton module directly) see a fresh value.
LOCK_PATH = defaults.LOCK_PATH


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
        return LeaderInfo.from_json(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError, KeyError):
        # Corrupt lockfile — treat as if no leader; the caller will overwrite it.
        return None


def write_lock(info: LeaderInfo, path: Path = LOCK_PATH) -> None:
    """Atomically replace the lockfile with ``info``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(info.to_json(), encoding="utf-8")
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
    if os.name == "nt":
        return _pid_is_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by someone else — still "alive" for
        # the purposes of "should I take over the lock".
        return True
    return True


def _pid_is_alive_windows(pid: int) -> bool:
    kernel32 = cast(Any, __import__("ctypes")).windll.kernel32
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return kernel32.GetLastError() == 5


def is_stale(info: LeaderInfo) -> bool:
    """A lock is stale when its recorded PID is no longer running.

    Liveness of the HTTP endpoint is checked separately by the caller — that
    requires an event loop, so we keep this function synchronous.
    """
    return not pid_is_alive(info.pid)


async def probe_http_alive(info: LeaderInfo, timeout: float = 2.0) -> bool:
    """Return True iff the leader's HTTP debugger answers ``/api/health`` quickly.

    A leader can have a live PID but a wedged event loop — the lockfile alone
    can't detect that. This is the second half of liveness; callers should
    combine it with :func:`is_stale` (PID check) to decide whether to take over.
    """
    import httpx

    url = f"http://{info.http_host}:{info.http_port}/api/health"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            return response.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


@contextlib.contextmanager
def election_lock(path: Path = LOCK_PATH, *, timeout: float = 10.0) -> Iterator[None]:
    """Serialise the leader-election decision across processes.

    Holds an exclusive ``fcntl.flock`` on ``<path>.election`` for the
    duration of the ``with`` block. Blocks (with backoff) until ``timeout``
    seconds, then raises ``TimeoutError``. On Windows (no ``fcntl``) the
    lock is a no-op — concurrent election is theoretically possible there
    but rare and self-corrects via the PID + HTTP probe.
    """
    if os.name == "nt":
        yield
        return
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    election_path = path.with_suffix(path.suffix + ".election")
    deadline = time.monotonic() + timeout
    fh = election_path.open("a+", encoding="utf-8")
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"timed out waiting {timeout:.1f}s for election lock at {election_path}"
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        fh.close()


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
