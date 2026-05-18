# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from octowright import bridge_state


def test_record_snapshot_writes_latest_by_pid(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"

    bridge_state.record_snapshot(
        path=path,
        follower_pid=123,
        remote_url="http://127.0.0.1:8765/mcp/",
        remote_session_id="sid-1",
        last_error=None,
        in_flight=0,
        reconnect_attempts=1,
        request_timeouts=0,
    )

    data = json.loads(path.read_text())
    assert data["followers"]["123"]["remote_url"] == "http://127.0.0.1:8765/mcp/"
    assert data["followers"]["123"]["remote_session_id"] == "sid-1"
    assert data["followers"]["123"]["in_flight"] == 0
    assert data["events"][-1]["event"] == "snapshot"


def test_record_snapshot_bounds_events(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"

    for i in range(12):
        bridge_state.record_snapshot(
            path=path,
            follower_pid=123,
            remote_url=f"http://127.0.0.1:{8765 + i}/mcp/",
            remote_session_id=f"sid-{i}",
            last_error=f"err-{i}",
            in_flight=i,
            reconnect_attempts=i,
            request_timeouts=i,
            max_events=5,
        )

    data = json.loads(path.read_text())
    assert len(data["events"]) == 5
    assert data["events"][0]["last_error"] == "err-7"
    assert data["events"][-1]["last_error"] == "err-11"
    assert data["followers"]["123"]["remote_session_id"] == "sid-11"


def test_read_state_returns_empty_shape_for_missing_file(tmp_path: Path) -> None:
    data = bridge_state.read_state(tmp_path / "missing.json")
    assert data == {"followers": {}, "events": []}


def test_read_state_recovers_from_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    path.write_text("{not json")
    data = bridge_state.read_state(path)
    assert data == {"followers": {}, "events": []}
