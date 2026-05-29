# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import itertools
import json
import time
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
    state["events"].append(snapshot)
    state["events"] = state["events"][-max_events:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{follower_pid}.{next(_TMP_COUNTER)}.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return
