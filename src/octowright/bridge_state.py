# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import contextlib
import errno
import itertools
import json
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright.version import VERSION

log = get_logger(__name__)

# What a snapshot written before followers reported their version looks like.
# Such a follower is stale BY DEFINITION -- the field was added in the same
# release that made staleness visible -- so the unknown bucket is counted as
# stale rather than excused.
UNKNOWN_FOLLOWER_VERSION = "unknown"

# Bound on the cross-process state-lock wait. Both callers run the locked
# transaction on an asyncio event loop, so an unbounded wait lets one frozen
# peer wedge every other process (see _acquire_bounded). Consts live here
# rather than defaults.py, which is at its LOC ceiling — the same convention
# recorder/sysresources/_heartbeat follow for their own knobs.
STATE_LOCK_TIMEOUT_SECONDS = 2.0
STATE_LOCK_POLL_SECONDS = 0.01

# Monotonic counter disambiguates concurrent snapshots (and survives PID reuse
# after a follower crash + OS PID recycle) so two writers can't collide on a
# single tmp filename and one silently overwrite the other's contents.
_TMP_COUNTER = itertools.count(1)


@dataclass
class _ThreadLockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, _ThreadLockEntry] = {}


@contextlib.contextmanager
def _thread_state_lock(path: Path, *, timeout: float | None = None) -> Iterator[bool]:
    """Boundedly serialize same-process threads before the OS file lock."""
    key = str(path.expanduser().absolute())
    with _THREAD_LOCKS_GUARD:
        entry = _THREAD_LOCKS.setdefault(key, _ThreadLockEntry())
        entry.users += 1
    acquired = False
    try:
        acquired = entry.lock.acquire(timeout=STATE_LOCK_TIMEOUT_SECONDS if timeout is None else max(0.0, timeout))
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


@contextlib.contextmanager
def _state_lock(path: Path) -> Iterator[bool]:
    """Exclusive cross-process lock for one read-modify-replace transaction.

    The atomic tmp-then-``os.replace`` write prevents torn JSON but NOT lost
    updates: two followers that both ``read_state`` before either writes will
    have the second write erase the first's registration (verified live — the
    dead-follower reaper then never learns about that follower). A blocking
    ``flock`` on a ``.lock`` sibling serializes the whole transaction.

    POSIX uses ``flock`` and Windows locks byte zero with ``msvcrt.locking``.
    A same-process thread lock is also required because Windows byte-range
    locks are process-scoped. The yielded boolean is true only while the OS
    lock is owned; callers must skip their transaction on timeout/open failure.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + STATE_LOCK_TIMEOUT_SECONDS
    with _thread_state_lock(lock_path, timeout=deadline - time.monotonic()) as thread_locked:
        if not thread_locked:
            log.warning(
                "octowright.bridge_state.thread_lock_timeout",
                path=str(lock_path),
                waited_seconds=STATE_LOCK_TIMEOUT_SECONDS,
            )
            yield False
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(lock_path, "a+b")  # noqa: SIM115 - handle owns lock lifetime
        except OSError as exc:
            log.warning("octowright.bridge_state.lock_open_failed", path=str(lock_path), error=repr(exc))
            yield False
            return
        locked = False
        try:
            try:
                locked = _acquire_bounded(fh, lock_path, deadline=deadline)
            except OSError as exc:
                log.warning("octowright.bridge_state.lock_failed", path=str(lock_path), error=repr(exc))
            yield locked
        finally:
            if locked:
                with contextlib.suppress(OSError):
                    if sys.platform == "win32":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()


def _acquire_bounded(fh: Any, lock_path: Path, *, deadline: float | None = None) -> bool:
    """Try to take the cross-process lock, giving up after a bounded wait.

    A *blocking* ``LOCK_EX`` here was a wedge: both callers run this
    synchronously on an asyncio event loop (the follower's reconnect coroutine
    and the leader's housekeeping job), and ``flock`` is NOT released when a
    process is SIGSTOPped — exactly what an MCP client does to a follower during
    compaction. One frozen peer mid-transaction would therefore block every
    other process's event loop for the length of the freeze — the price of the
    lock, against wait-free writes where no process can block another.

    Poll non-blocking until the deadline, then return ``False`` so the caller
    skips that snapshot/removal. A later heartbeat or housekeeping pass retries
    naturally, preserving responsiveness without reopening the lost-update
    window this lock exists to close.
    """
    deadline = time.monotonic() + STATE_LOCK_TIMEOUT_SECONDS if deadline is None else deadline
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
            # Retry only real contention. Unsupported/broken filesystems must
            # fail immediately instead of blocking an event loop every pass.
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                raise
            if time.monotonic() >= deadline:
                log.warning(
                    "octowright.bridge_state.lock_timeout",
                    path=str(lock_path),
                    waited_seconds=STATE_LOCK_TIMEOUT_SECONDS,
                )
                return False
            time.sleep(STATE_LOCK_POLL_SECONDS)


async def record_snapshot_async(**kwargs: Any) -> None:
    """Offload the bounded synchronous lock transaction from an event loop."""
    await asyncio.to_thread(record_snapshot, **kwargs)


async def remove_followers_async(path: Path, pids: Iterable[int]) -> None:
    """Offload follower removal and materialize ``pids`` before the worker."""
    await asyncio.to_thread(remove_followers, path, tuple(pids))


def _empty_state() -> dict[str, Any]:
    return {"followers": {}, "events": []}


def read_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    followers = raw.get("followers")
    events = raw.get("events")
    if not isinstance(followers, dict) or not isinstance(events, list):
        return _empty_state()
    return {"followers": followers, "events": events}


def _follower_totals(followers: dict[str, Any]) -> tuple[int, int, int, str | None]:
    """Summed counters plus the newest non-empty ``last_error`` across followers."""
    latest_error: str | None = None
    latest_ts: float | None = None
    totals = [0, 0, 0]
    for item in followers.values():
        if not isinstance(item, dict):
            continue
        for index, key in enumerate(("in_flight", "reconnect_attempts", "request_timeouts")):
            totals[index] += _int_value(item.get(key))
        error = item.get("last_error")
        ts = item.get("ts")
        if isinstance(error, str) and error and isinstance(ts, (int, float)) and (latest_ts is None or ts >= latest_ts):
            latest_error = error
            latest_ts = float(ts)
    return totals[0], totals[1], totals[2], latest_error


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID exists.

    Delegates to :func:`octowright.singleton.pid_is_alive`, which is
    cross-platform — on Windows it uses ``OpenProcess`` rather than
    ``os.kill(pid, 0)`` (which never reports a dead PID on Windows, so a
    POSIX-only check treated every dead follower as alive and never pruned).
    Conservative: ambiguous outcomes (permission denied) count as ALIVE so live
    followers are never pruned; an unusable/overflowing PID prunes.
    """
    from octowright.singleton import pid_is_alive

    try:
        return pid_is_alive(pid)
    except (OverflowError, ValueError):
        return False


def _partition_live(followers: dict[str, Any], is_alive: Callable[[int], bool]) -> tuple[dict[str, Any], int]:
    """Split recorded followers into (live, dead_count) by PID liveness.

    ``_prune_dead_followers`` already drops dead entries, but only when a
    follower WRITES a snapshot. Nothing prunes on the read path, so a reader
    sees a dead follower for as long as no follower happens to write -- and a
    follower that has stopped writing is exactly the one most likely to be
    dead. Observed live: two entries reported as stale followers "running older
    code", both of which were already-exited processes; acting on that count
    meant chasing ghosts.

    An unparsable PID key is treated as LIVE, matching ``_prune_dead_followers``:
    the conservative direction is to over-report a follower, not to silently
    drop one that exists.
    """
    live: dict[str, Any] = {}
    dead = 0
    for key, snap in followers.items():
        try:
            alive = is_alive(int(key))
        except (TypeError, ValueError):
            alive = True
        if alive:
            live[key] = snap
        else:
            dead += 1
    return live, dead


def summarize_state(state: dict[str, Any], *, is_alive: Callable[[int], bool] | None = None) -> dict[str, Any]:
    """Summarize bridge state, counting only followers whose PID is still alive.

    ``is_alive`` is injectable so tests can be deterministic: the default issues
    a real liveness syscall per follower (cheap -- ``os.kill(pid, 0)``), which
    would otherwise make a fixture's synthetic PIDs machine-dependent.

    Resolved at CALL time rather than as a default argument value. A default of
    ``is_alive=_pid_alive`` binds the function object when this module is
    imported, so monkeypatching ``bridge_state._pid_alive`` would silently have
    no effect -- a trap for any caller's test that patches the obvious name.
    """
    check = is_alive if is_alive is not None else _pid_alive
    followers = state.get("followers")
    events = state.get("events")
    if not isinstance(followers, dict):
        followers = {}
    if not isinstance(events, list):
        events = []

    followers, dead_followers = _partition_live(followers, check)
    in_flight, reconnect_attempts, request_timeouts, latest_error = _follower_totals(followers)
    versions = _follower_version_counts(followers)
    stale = sum(count for version, count in versions.items() if version != VERSION)
    return {
        "follower_count": len(followers),
        # Recorded-but-exited followers, dropped from every count above. Surfaced
        # rather than silently discarded so a shrinking follower_count is
        # explainable instead of looking like followers vanishing.
        "dead_follower_count": dead_followers,
        "event_count": len(events),
        "total_in_flight": in_flight,
        "total_reconnect_attempts": reconnect_attempts,
        "total_request_timeouts": request_timeouts,
        "latest_error": latest_error,
        # Version skew. The leader answers this call, so ``VERSION`` is the
        # leader's own -- a follower reporting anything else is running code
        # the running daemon is not, and only ITS client restarting can
        # change that (killing the subprocess just breaks that client's
        # session until the same manual reconnect).
        "leader_version": VERSION,
        "follower_versions": versions,
        "stale_follower_count": stale,
        # A count alone leaves the reader with nothing to do, and the action is
        # not the obvious one: restarting the DAEMON cannot fix this, since a
        # follower survives that by design. Only its own client respawning it
        # can.
        "stale_follower_hint": _STALE_FOLLOWER_HINT if stale else None,
    }


_STALE_FOLLOWER_HINT = (
    "These followers are running older code than the leader. A daemon restart cannot update them -- "
    "a follower is a subprocess its MCP client owns and it survives a leader restart by design. "
    "Each client must reconnect to octowright (Claude Code: /mcp -> octowright -> Reconnect) to spawn "
    "a fresh follower. Leader-side behaviour is already current; this only affects follower-side code."
)


def _follower_version_counts(followers: dict[str, Any]) -> dict[str, int]:
    """Followers per reported version, newest-schema first, sorted for stability."""
    counts: dict[str, int] = {}
    for item in followers.values():
        version = item.get("follower_version") if isinstance(item, dict) else None
        key = version if isinstance(version, str) and version else UNKNOWN_FOLLOWER_VERSION
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _prune_dead_followers(followers: dict[str, Any], *, keep_pid: int) -> dict[str, Any]:
    """Drop followers whose PID is no longer alive, always keeping ``keep_pid``
    (the follower currently recording — alive by definition). This bounds the
    registry to live followers instead of accumulating every PID ever seen.
    """
    keep_key = str(keep_pid)
    kept: dict[str, Any] = {}
    for key, snap in followers.items():
        if key == keep_key:
            kept[key] = snap
            continue
        try:
            alive = _pid_alive(int(key))
        except (TypeError, ValueError):
            alive = True  # unparsable PID key -> keep (conservative)
        if alive:
            kept[key] = snap
    return kept


def bounded_view(state: dict[str, Any], *, max_followers: int = 25, max_events: int = 20) -> dict[str, Any]:
    """A size-bounded projection of bridge state, safe to embed in status output.

    Keeps the most-recent ``max_followers`` followers (by ``ts``) and the last
    ``max_events`` events, and sets ``followers_truncated`` when followers were
    dropped. The TRUE follower count stays available via ``summarize_state`` —
    this only bounds the raw dump so a stale-follower leak (or a burst of live
    followers) can't blow the status payload.
    """
    followers = state.get("followers")
    events = state.get("events")
    if not isinstance(followers, dict):
        followers = {}
    if not isinstance(events, list):
        events = []
    truncated = len(followers) > max_followers
    if truncated:

        def _ts(item: tuple[str, Any]) -> float:
            snap = item[1]
            ts = snap.get("ts") if isinstance(snap, dict) else None
            return float(ts) if isinstance(ts, (int, float)) else 0.0

        followers = dict(sorted(followers.items(), key=_ts, reverse=True)[:max_followers])
    return {
        "followers": followers,
        "events": events[-max_events:],
        "followers_truncated": truncated,
    }


def record_snapshot(
    *,
    path: Path,
    follower_pid: int,
    remote_url: str | None,
    remote_session_id: str | None,
    last_error: str | None,
    in_flight: int,
    reconnect_attempts: int,
    request_timeouts: int,
    max_events: int = 50,
    follower_version: str = VERSION,
) -> None:
    """Record one follower's bridge snapshot.

    ``follower_version`` defaults to this process's own version because the
    only caller is a follower describing itself. It exists because a follower
    is a subprocess its MCP CLIENT owns and supervises: it survives a leader
    restart by design (that is what the leader-recovery window is for), so a
    daemon restart can never deploy follower-side code, and the leader had no
    way to tell a three-day-old follower from one spawned a minute ago -- the
    self-identifying header carries a pid and nothing else. Diagnosing a
    version skew meant reading process start times against commit timestamps
    by hand.
    """
    snapshot = {
        "ts": time.time(),
        "event": "snapshot",
        "follower_pid": follower_pid,
        "follower_version": follower_version,
        "remote_url": remote_url,
        "remote_session_id": remote_session_id,
        "last_error": last_error,
        "in_flight": in_flight,
        "reconnect_attempts": reconnect_attempts,
        "request_timeouts": request_timeouts,
    }
    with _state_lock(path) as locked:
        if not locked:
            return
        state = read_state(path)
        state["followers"][str(follower_pid)] = snapshot
        state["followers"] = _prune_dead_followers(state["followers"], keep_pid=follower_pid)
        state["events"].append(snapshot)
        state["events"] = state["events"][-max_events:]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + f".{follower_pid}.{next(_TMP_COUNTER)}.tmp")
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            return


# A live write's tmp sibling exists for microseconds (write, then os.replace).
# Anything older than this was orphaned by a process killed between the two —
# a crash, SIGKILL, or host restart mid-write — not a write in flight. Wide
# margin so this can never race a genuinely in-progress write.
_STALE_TMP_AGE_SECONDS = 300.0


def sweep_stale_tmp_files(path: Path, *, max_age_seconds: float = _STALE_TMP_AGE_SECONDS) -> list[str]:
    """Delete leftover atomic-write tmp siblings older than ``max_age_seconds``.

    ``record_snapshot`` / ``remove_followers`` write via a temp-sibling-then-
    ``os.replace``, which normally leaves no tmp file behind — but a process
    killed between the write and the replace orphans one permanently (found
    364 of these, some weeks old, on 2026-07-09 after repeated ungraceful
    daemon deaths; hand-cleaned then, unbounded again since nothing swept
    them automatically). Best-effort: a stat/unlink race with another writer
    is swallowed, not raised.
    """
    removed: list[str] = []
    try:
        candidates = list(path.parent.glob(f"{path.name}.*.tmp"))
    except OSError:
        return removed
    now = time.time()
    for tmp in candidates:
        try:
            if now - tmp.stat().st_mtime < max_age_seconds:
                continue
            tmp.unlink()
        except OSError:
            continue
        removed.append(tmp.name)
    return removed


def remove_followers(path: Path, pids: Iterable[int]) -> None:
    """Drop specific follower entries by PID from the shared state file.

    Used by the leader's dead-follower session reaper (``housekeeping``) after
    it terminates a dead follower's MCP session, so the entry doesn't linger
    pointing at a session that no longer exists. Best-effort and idempotent —
    a no-op if another writer already dropped the same entries (matches
    ``record_snapshot``'s atomic tmp-then-replace pattern).
    """
    keys = {str(pid) for pid in pids}
    if not keys:
        return
    with _state_lock(path) as locked:
        if not locked:
            return
        state = read_state(path)
        followers = state.get("followers", {})
        if not any(key in followers for key in keys):
            return
        state["followers"] = {key: snap for key, snap in followers.items() if key not in keys}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + f".reaper.{next(_TMP_COUNTER)}.tmp")
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            return
