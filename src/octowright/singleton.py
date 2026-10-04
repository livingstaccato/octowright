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
across processes by ``election_lock``, an advisory lock on a sibling
lockfile — ``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows. Without
it, two simultaneous starters could both observe "no live leader" and both
spawn a daemon; the second daemon would silently bind a different port and
leave followers bridging to the abandoned one. Reproduced live on Windows
with 8 concurrent starters and no lock: all 8 observed "no live leader" and
all 8 spawned a competing daemon for the same canonical port — not a rare
edge case once real concurrency is applied, which is why Windows gets a real
lock rather than relying on the PID + HTTP probe to self-correct after the
fact.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Collection, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import anyio
from provide.telemetry import get_logger

from octowright import defaults

log = get_logger(__name__)

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
    # Capability token gating the /mcp transport. Held only in this 0600 lockfile;
    # the follower presents it, the leader verifies it. Default "" keeps a
    # pre-upgrade lockfile (no token key) parseable and disables the gate.
    token: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> LeaderInfo:
        data = json.loads(s)
        return cls(**data)


def read_lock(path: Path = LOCK_PATH) -> LeaderInfo | None:
    """Return the parsed lockfile, or None if it is absent, unreadable or corrupt.

    Never raises for a bad lockfile: this is called on every boot and every
    follower reconnect, and the file is same-user-writable and replaced or
    unlinked by other processes at any moment. There is no existence check
    first -- that was a check-then-read race (a leader removing its lock in
    between escaped as ``FileNotFoundError``) -- so the read itself decides.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        # Present but unreadable (permissions, a directory, I/O error): there is
        # no leader we can use, but unlike absence it is worth a line in the log.
        log.warning("singleton.lock_unreadable", path=str(path), error=str(exc))
        return None
    except UnicodeDecodeError:
        return None
    try:
        return LeaderInfo.from_json(raw)
    except (ValueError, TypeError, KeyError):
        # Corrupt lockfile (bad JSON, wrong shape) -- treat as if no leader; the
        # caller will overwrite it. JSONDecodeError is a ValueError.
        return None


def write_lock(info: LeaderInfo, path: Path = LOCK_PATH) -> None:
    """Atomically replace the lockfile with ``info``.

    The lockfile records the leader's PID and HTTP-MCP endpoint URL —
    sensitive enough that other local users (shared host, multi-tenant
    workstation) shouldn't be able to read or tamper with it, and the bridge
    capability token. The file is created ``0o600`` (never widened by the
    umask, even briefly) and the parent dir forced to ``0o700``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # chmod is a no-op on Windows for mode bits beyond read-only; the
    # invocation is still safe and keeps the POSIX path single-branched.
    # Verified not a gap in practice -- see private_paths.py's module
    # docstring for the icacls evidence: a Windows per-user profile ACL
    # already excludes every other local account from this file's parent
    # tree, chmod or no chmod.
    if os.name != "nt":
        try:
            path.parent.chmod(0o700)
        except OSError as exc:
            # A pre-existing parent we don't own (OCTOWRIGHT_LOCK_PATH pointed
            # into a shared dir) can't be tightened. The file is still created
            # private below, but a directory other users can write lets them
            # replace or unlink the lockfile, so say so rather than swallow it.
            log.warning("singleton.lock_parent_chmod_failed", path=str(path.parent), error=str(exc))
    # The token is in this file, so it has to be private from the first byte:
    # mkstemp creates it 0600 with O_EXCL, whatever the umask, under a random
    # name. The old ``<pid>.tmp`` was written under the umask (usually 0644)
    # and chmodded afterwards -- readable in between -- and its predictable
    # name let another user pre-plant a symlink for the write to follow.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(info.to_json())
        tmp.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


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
    # Read GetLastError BEFORE any other Win32 call: CloseHandle (and even
    # success paths inside the runtime) can clobber the thread-local last-error
    # code, turning ERROR_ACCESS_DENIED (process exists, owned by another user)
    # into "looks dead".
    last_error = kernel32.GetLastError()
    return last_error == 5


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
    import httpx2

    url = f"http://{info.http_host}:{info.http_port}/api/health"
    try:
        async with httpx2.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            return response.status_code == 200
    except (httpx2.HTTPError, OSError):
        return False


def _election_paths(path: Path) -> Path:
    """Return the sibling ``.election`` lock path, creating its parent dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.with_suffix(path.suffix + ".election")


def _try_flock(fh: Any) -> bool:
    """Attempt a non-blocking exclusive flock. Return True on success.

    Imported here to keep the import surface narrow and isolate the
    fcntl-on-Windows guard at the call sites (we don't reach this when
    ``os.name == 'nt'``).
    """
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _release_flock(fh: Any) -> None:
    """Best-effort flock release; swallow OSError during teardown."""
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _try_msvcrt_lock(fh: Any) -> bool:
    """Windows equivalent of :func:`_try_flock`: a non-blocking exclusive
    lock on a single byte of the sibling lockfile, via ``msvcrt.locking``.

    ``msvcrt.locking`` locks a BYTE RANGE from the file's current position,
    not the whole file like ``flock`` -- so every caller must seek to the
    same offset (0) first, which is why this and :func:`_release_msvcrt_lock`
    both do it. Verified empirically (not assumed): a second handle to the
    same path raises ``PermissionError`` (an ``OSError``) on contention, and
    reacquires cleanly once the first releases -- see the election-lock
    tests for the reproduction this backs.

    The ``sys.platform`` guard (rather than this module's usual ``os.name``)
    is deliberate: mypy special-cases ``sys.platform`` checks and prunes the
    branch that doesn't match the platform IT runs on, so the msvcrt-only
    branch is simply never type-checked on Linux/macOS CI -- where msvcrt's
    typeshed stub is empty and ``locking``/``LK_NBLCK`` would otherwise be
    "no attribute" errors. ``os.name`` gets no such special-casing, which is
    why the fcntl calls elsewhere in this file DO error under a Windows-run
    mypy (pre-existing, invisible to CI since the Lint job runs on Linux).
    """
    if sys.platform != "win32":
        raise NotImplementedError("Windows only; callers dispatch on os.name")  # pragma: no cover
    import msvcrt

    fh.seek(0)
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


def _release_msvcrt_lock(fh: Any) -> None:
    """Best-effort ``msvcrt`` lock release; swallow OSError during teardown.

    See :func:`_try_msvcrt_lock` for why this guards on ``sys.platform``
    rather than ``os.name``.
    """
    if sys.platform != "win32":
        return  # pragma: no cover
    import msvcrt

    fh.seek(0)
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def _try_election_lock(fh: Any) -> bool:
    """Platform-dispatching non-blocking exclusive lock attempt."""
    return _try_msvcrt_lock(fh) if os.name == "nt" else _try_flock(fh)


def _release_election_lock(fh: Any) -> None:
    """Platform-dispatching lock release."""
    if os.name == "nt":
        _release_msvcrt_lock(fh)
    else:
        _release_flock(fh)


@contextlib.contextmanager
def election_lock(path: Path = LOCK_PATH, *, timeout: float = 10.0) -> Iterator[None]:
    """Synchronous leader-election lock.

    Kept for non-async callers; the async path should use
    :func:`async_election_lock` instead so the event loop isn't blocked
    by the ``time.sleep`` back-off on contention.

    Implementation shares the open/lock loop with :func:`async_election_lock`
    so a timeout-logic change applied to one automatically applies to both.
    Only the sleep primitive differs: sync uses ``time.sleep``; async uses
    ``anyio.sleep``. Locking itself is platform-dispatched by
    :func:`_try_election_lock` (``fcntl.flock`` / ``msvcrt.locking``), so
    this function has no platform branch of its own.
    """
    election_path = _election_paths(path)
    deadline = time.monotonic() + timeout
    fh = election_path.open("a+", encoding="utf-8")
    try:
        while not _try_election_lock(fh):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting {timeout:.1f}s for election lock at {election_path}") from None
            time.sleep(0.05)
        try:
            yield
        finally:
            _release_election_lock(fh)
    finally:
        fh.close()


@contextlib.asynccontextmanager
async def async_election_lock(path: Path = LOCK_PATH, *, timeout: float = 10.0) -> AsyncIterator[None]:
    """Async-friendly version of :func:`election_lock`.

    Uses ``anyio.sleep`` for back-off so contention doesn't stall the
    event loop. Shares :func:`_try_election_lock` / :func:`_release_election_lock`
    with :func:`election_lock` so the acquire/release semantics -- and the
    platform dispatch -- stay in lock-step across the two.
    """
    election_path = _election_paths(path)
    deadline = time.monotonic() + timeout
    fh = election_path.open("a+", encoding="utf-8")
    try:
        while not _try_election_lock(fh):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting {timeout:.1f}s for election lock at {election_path}") from None
            await anyio.sleep(0.05)
        try:
            yield
        finally:
            _release_election_lock(fh)
    finally:
        fh.close()


def make_leader_info(http_host: str, http_port: int, *, token: str = "") -> LeaderInfo:
    """Build the lockfile record for ``this`` process becoming leader.

    ``token`` is the bridge capability token, generated once by the caller
    (``cli/serve``) so the exact same value is both written to the 0600 lockfile
    here and handed to the /mcp guard at app-build time.

    Also stashes a monotonic-clock timestamp in process-local state so that
    callers in this same process can compute uptime without wall-clock skew
    contaminating the result. The stash is private to this Python process;
    foreign callers (a separate process reading the lockfile) get the
    wall-clock ``started_at`` and pay the clock-skew cost.
    """
    global _LOCAL_LEADER_STARTED_MONOTONIC
    _LOCAL_LEADER_STARTED_MONOTONIC = time.monotonic()
    return LeaderInfo(
        pid=os.getpid(),
        http_host=http_host,
        http_port=http_port,
        # Trailing slash matters: Starlette's Mount strips ``/mcp`` and routes
        # the remainder. The streamable-http app's inner route is ``/``, so the
        # client must POST to ``/mcp/`` (a bare ``/mcp`` returns 405).
        mcp_url=f"http://{http_host}:{http_port}/mcp/",
        started_at=time.time(),
        token=token,
    )


_LOCAL_LEADER_STARTED_MONOTONIC: float | None = None


def local_leader_started_monotonic() -> float | None:
    """Return the monotonic timestamp at which this process became leader.

    Returns ``None`` if this process never called :func:`make_leader_info`
    (e.g. a follower, or a fresh interpreter). Callers should only use this
    when ``read_lock().pid == os.getpid()`` — across processes the monotonic
    clocks are not comparable.
    """
    return _LOCAL_LEADER_STARTED_MONOTONIC


# How long a leader shutting down waits for the election lock before leaving
# its lockfile alone. Contention means another process is electing right now,
# and that process decides what the lockfile says next; a lock left naming our
# dead pid reads as stale to every reader anyway. Short on purpose: the usual
# holder is ``octowright restart``, which keeps the lock across the whole kill
# and removes a lockfile naming a pid it stopped itself, so a longer wait only
# delayed every restarted leader's exit (2s, which a short ``--timeout`` turned
# into a SIGKILL). This much rides out a follower's brief probe-and-release.
RELEASE_LOCK_TIMEOUT_SECONDS = 0.25


def remove_lock_if_owned(owner_pids: Collection[int], *, token: str | None = None, path: Path = LOCK_PATH) -> bool:
    """Unlink the lockfile only if it still names one of ``owner_pids``.

    The lockfile is the one record of which leader is live, and it is replaced
    by every new leader. Unlinking it unconditionally let a superseded leader
    (or ``restart``, after killing one) erase a SUCCESSOR's record: the next
    client then found no leader and a free canonical port and spawned a third,
    while the successor kept running invisible to ``restart`` -- split-brain.
    ``token``, when given, must match as well, so a pid the OS recycled to a
    successor is not mistaken for the owner. The caller should hold the
    election lock so the check and the unlink cannot straddle a new write.
    Returns whether the file was removed.
    """
    info = read_lock(path)
    if info is None or info.pid not in owner_pids:
        return False
    if token is not None and info.token != token:
        return False
    remove_lock(path)
    return True


async def release_own_lock(
    token: str, *, path: Path = LOCK_PATH, timeout: float = RELEASE_LOCK_TIMEOUT_SECONDS
) -> bool:
    """A leader's shutdown removal of its lockfile: under the election lock, and
    only while the file still describes this process (pid and token)."""
    try:
        async with async_election_lock(path, timeout=timeout):
            return remove_lock_if_owned({os.getpid()}, token=token, path=path)
    except TimeoutError:
        log.warning("singleton.lock_release_skipped", reason="election lock held by another process")
        return False
