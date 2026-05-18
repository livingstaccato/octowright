# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


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
        tmp = path.with_suffix(path.suffix + f".{follower_pid}.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return
