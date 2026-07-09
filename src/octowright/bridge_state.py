# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import itertools
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Monotonic counter disambiguates concurrent snapshots (and survives PID reuse
# after a follower crash + OS PID recycle) so two writers can't collide on a
# single tmp filename and one silently overwrite the other's contents.
_TMP_COUNTER = itertools.count(1)


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


def summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    followers = state.get("followers")
    events = state.get("events")
    if not isinstance(followers, dict):
        followers = {}
    if not isinstance(events, list):
        events = []

    latest_error: str | None = None
    latest_ts: float | None = None
    total_in_flight = 0
    total_reconnect_attempts = 0
    total_request_timeouts = 0

    for item in followers.values():
        if not isinstance(item, dict):
            continue
        total_in_flight += _int_value(item.get("in_flight"))
        total_reconnect_attempts += _int_value(item.get("reconnect_attempts"))
        total_request_timeouts += _int_value(item.get("request_timeouts"))
        error = item.get("last_error")
        ts = item.get("ts")
        if isinstance(error, str) and error and isinstance(ts, (int, float)) and (latest_ts is None or ts >= latest_ts):
            latest_error = error
            latest_ts = float(ts)

    return {
        "follower_count": len(followers),
        "event_count": len(events),
        "total_in_flight": total_in_flight,
        "total_reconnect_attempts": total_reconnect_attempts,
        "total_request_timeouts": total_request_timeouts,
        "latest_error": latest_error,
    }


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


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
) -> None:
    snapshot = {
        "ts": time.time(),
        "event": "snapshot",
        "follower_pid": follower_pid,
        "remote_url": remote_url,
        "remote_session_id": remote_session_id,
        "last_error": last_error,
        "in_flight": in_flight,
        "reconnect_attempts": reconnect_attempts,
        "request_timeouts": request_timeouts,
    }
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
