# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Lightweight launch manifest for crash/orphan diagnostics.

This is intentionally not a reattach registry. It records enough state to
explain stale sessions after a daemon crash and is cleared on graceful close.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import itertools
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ParamSpec, TypeVar, cast

import anyio

from octowright.defaults import SESSION_MANIFEST_PATH
from octowright.types import SessionManifest, SessionManifestEntry

SCHEMA_VERSION = 1
MANIFEST_LOCK_TIMEOUT_SECONDS = 2.0
MANIFEST_LOCK_POLL_SECONDS = 0.01

_TMP_COUNTER = itertools.count(1)


@dataclass
class _ThreadLockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, _ThreadLockEntry] = {}

_P = ParamSpec("_P")
_T = TypeVar("_T")


async def wait_task_after_cancellation(task: asyncio.Task[_T]) -> _T:
    """Join ``task`` despite AnyIO level cancellation or repeated Task.cancel.

    The caller has already decided to propagate cancellation after the join.
    Each direct cancellation request is consumed only long enough to preserve
    the cleanup/transaction ordering invariant.
    """
    current = asyncio.current_task()
    while not task.done():
        try:
            with anyio.CancelScope(shield=True):
                await asyncio.shield(task)
        except asyncio.CancelledError:
            if current is not None:
                current.uncancel()
    return task.result()


async def run_manifest_transaction_async(func: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Run a synchronous manifest transaction without blocking the event loop.

    Cancellation waits for the worker to finish before propagating. Otherwise a
    launch cleanup could remove a row while its canceled record worker later
    recreates it, or a keep-id registry rekey could diverge from its manifest.
    """
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        if (current := asyncio.current_task()) is not None:
            current.uncancel()
        try:
            await wait_task_after_cancellation(task)
        except Exception as exc:
            raise cancelled from exc
        raise


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _empty() -> SessionManifest:
    return {"schema_version": SCHEMA_VERSION, "sessions": {}}


def _resolve_path(path: Path | None) -> Path:
    return path or SESSION_MANIFEST_PATH


@contextlib.contextmanager
def _thread_manifest_lock(path: Path, *, timeout: float) -> Iterator[bool]:
    """Boundedly own one ref-counted process-local manifest lock."""
    key = str(path.expanduser().absolute())
    with _THREAD_LOCKS_GUARD:
        entry = _THREAD_LOCKS.setdefault(key, _ThreadLockEntry())
        entry.users += 1
    acquired = False
    try:
        acquired = entry.lock.acquire(timeout=max(0.0, timeout))
        yield acquired
    finally:
        if acquired:
            entry.lock.release()
        with _THREAD_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0 and _THREAD_LOCKS.get(key) is entry:
                del _THREAD_LOCKS[key]


def _prepare_windows_lock_file(fh: Any) -> None:
    """Ensure byte zero exists because ``msvcrt.locking`` locks byte ranges."""
    fh.seek(0, 2)
    if fh.tell() == 0:
        fh.write(b"\0")
        fh.flush()
    fh.seek(0)


def _acquire_file_lock(fh: Any, *, deadline: float) -> bool:
    if sys.platform == "win32":
        import msvcrt

        _prepare_windows_lock_file(fh)
    else:
        import fcntl
    while True:
        try:
            if sys.platform == "win32":
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            # Non-blocking POSIX flock reports EAGAIN/EACCES when another
            # process owns the lock. ``msvcrt.locking`` commonly reports
            # EACCES for the same condition. Other errors (read-only/broken
            # filesystem, invalid descriptor, unsupported operation) will not
            # improve with polling, so surface them immediately.
            if not isinstance(exc, BlockingIOError) and exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            if time.monotonic() >= deadline:
                return False
            time.sleep(MANIFEST_LOCK_POLL_SECONDS)


def _release_file_lock(fh: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _manifest_lock(path: Path) -> Iterator[None]:
    """Serialize one manifest transaction across threads and processes.

    Atomic replacement prevents torn JSON, but only a stable sibling lock can
    prevent two read-modify-write callers from replacing each other's updates.
    A bounded failure raises instead of entering the transaction unlocked.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + MANIFEST_LOCK_TIMEOUT_SECONDS
    with _thread_manifest_lock(lock_path, timeout=deadline - time.monotonic()) as thread_locked:
        if not thread_locked:
            raise TimeoutError(f"timed out acquiring manifest lock {lock_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as fh:
            if not _acquire_file_lock(fh, deadline=deadline):
                raise TimeoutError(f"timed out acquiring manifest lock {lock_path}")
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    _release_file_lock(fh)


def read_manifest(path: Path | None = None) -> SessionManifest:
    """Return a parsed manifest, or an empty manifest if missing/corrupt."""
    path = _resolve_path(path)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        return _empty()
    return {"schema_version": data.get("schema_version", SCHEMA_VERSION), "sessions": sessions}


def _write_manifest_unlocked(data: SessionManifest, path: Path) -> None:
    """Atomically replace the manifest while its transaction lock is held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{next(_TMP_COUNTER)}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def write_manifest(data: SessionManifest, path: Path | None = None) -> None:
    """Atomically replace the manifest under the shared writer lock."""
    resolved = _resolve_path(path)
    with _manifest_lock(resolved):
        _write_manifest_unlocked(data, resolved)


def record_launch(
    *,
    session_id: str,
    kind: str,
    label: str | None,
    profile: str | None,
    user_data_dir: str | Path | None,
    log_path: str | Path,
    path: Path | None = None,
) -> SessionManifestEntry:
    """Add/update an open session entry and return the stored entry."""
    resolved = _resolve_path(path)
    with _manifest_lock(resolved):
        manifest = read_manifest(resolved)
        launched_at = _now_iso()
        entry: SessionManifestEntry = {
            "session_id": session_id,
            "kind": kind,
            "label": label,
            "profile": profile,
            "user_data_dir": str(user_data_dir) if user_data_dir is not None else None,
            "log_path": str(log_path),
            "launched_at": launched_at,
            "updated_at": launched_at,
            "state": "open",
            "daemon_pid": os.getpid(),
        }
        manifest["sessions"][session_id] = entry
        _write_manifest_unlocked(manifest, resolved)
    return entry


def remove_session(session_id: str, path: Path | None = None) -> bool:
    """Remove a gracefully closed session entry. Returns True when removed."""
    resolved = _resolve_path(path)
    with _manifest_lock(resolved):
        manifest = read_manifest(resolved)
        removed = manifest["sessions"].pop(session_id, None) is not None
        if removed:
            _write_manifest_unlocked(manifest, resolved)
        return removed


def rekey_session(source_session_id: str, target_session_id: str, path: Path | None = None) -> bool:
    """Move a live entry to the client-facing id used after keep-id relaunch."""
    if source_session_id == target_session_id:
        return True
    resolved = _resolve_path(path)
    with _manifest_lock(resolved):
        manifest = read_manifest(resolved)
        raw = manifest["sessions"].get(source_session_id)
        if not isinstance(raw, dict):
            return False
        del manifest["sessions"][source_session_id]
        entry = cast(
            SessionManifestEntry,
            {**raw, "session_id": target_session_id, "updated_at": _now_iso()},
        )
        manifest["sessions"][target_session_id] = entry
        _write_manifest_unlocked(manifest, resolved)
        return True


def _pid_alive(pid: int) -> bool:
    """True if the OS still has a process with this PID.

    Delegates to :func:`octowright.singleton.pid_is_alive`, which handles the
    Windows case properly: there ``os.kill(pid, 0)`` raises ``OSError``
    (WinError 87) for a dead PID rather than ``ProcessLookupError``, so a naive
    probe reports every dead daemon as alive and nothing is ever pruned.
    Failing closed (treating an unknown PID as alive) keeps a stale entry,
    which is the harmless direction.
    """
    from octowright.singleton import pid_is_alive

    try:
        return pid_is_alive(pid)
    except Exception:
        return True


def prune_dead_daemon_entries(current_pid: int | None = None, path: Path | None = None) -> list[str]:
    """Drop entries stranded by a daemon generation that is gone. Returns the ids.

    ``remove_session`` only runs on a graceful close, so every entry that was
    open when a daemon was SIGKILLed (``octowright restart``, a crash, an OOM
    kill) is stranded permanently — nothing else reaps them, and they surface as
    phantom sessions in diagnostics.

    Orphanhood is decided by the recorded ``daemon_pid``, NOT by absence from
    the live pool: this runs at leader boot when the pool is empty, so
    pool-absence alone would flag every entry, including ones a concurrently
    live daemon owns. Conservative by construction — an entry is removed only
    when its owning pid is *provably* gone, so a recycled pid or a missing
    ``daemon_pid`` (pre-schema entries) leaves a stale entry rather than
    deleting a live one.
    """
    resolved = _resolve_path(path)
    with _manifest_lock(resolved):
        manifest = read_manifest(resolved)
        current_pid = os.getpid() if current_pid is None else current_pid
        removed: list[str] = []
        for session_id, raw in sorted(manifest["sessions"].items()):
            if not isinstance(raw, dict):
                continue
            pid = raw.get("daemon_pid")
            if not isinstance(pid, int) or pid == current_pid or _pid_alive(pid):
                continue
            removed.append(session_id)
        if not removed:
            return []
        for session_id in removed:
            del manifest["sessions"][session_id]
        _write_manifest_unlocked(manifest, resolved)
        return removed


def stale_entries(
    *,
    live_session_ids: set[str],
    path: Path | None = None,
) -> list[SessionManifestEntry]:
    """Return manifest entries that are not present in the current live pool."""
    manifest = read_manifest(path)
    stale: list[SessionManifestEntry] = []
    for session_id, raw in sorted(manifest["sessions"].items()):
        if session_id in live_session_ids or not isinstance(raw, dict):
            continue
        entry = cast(SessionManifestEntry, {**raw})
        entry.setdefault("session_id", session_id)
        entry["reason"] = "manifest entry is not present in the live browser pool"
        stale.append(entry)
    return stale
