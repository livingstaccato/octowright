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


def test_summarize_state_totals_followers_and_latest_error() -> None:
    data = {
        "followers": {
            "1": {
                "ts": 10.0,
                "last_error": "older",
                "in_flight": 1,
                "reconnect_attempts": 2,
                "request_timeouts": 3,
            },
            "2": {
                "ts": 20.0,
                "last_error": "newer",
                "in_flight": 4,
                "reconnect_attempts": 5,
                "request_timeouts": 6,
            },
        },
        "events": [{"event": "snapshot"}, {"event": "snapshot"}],
    }

    assert bridge_state.summarize_state(data) == {
        "follower_count": 2,
        "event_count": 2,
        "total_in_flight": 5,
        "total_reconnect_attempts": 7,
        "total_request_timeouts": 9,
        "latest_error": "newer",
    }


def test_summarize_state_ignores_bad_shapes() -> None:
    data = {
        "followers": {
            "bad": "not a snapshot",
            "ok": {
                "ts": 1.0,
                "last_error": "",
                "in_flight": -1,
                "reconnect_attempts": "many",
                "request_timeouts": 2,
            },
        },
        "events": "not events",
    }

    assert bridge_state.summarize_state(data) == {
        "follower_count": 2,
        "event_count": 0,
        "total_in_flight": 0,
        "total_reconnect_attempts": 0,
        "total_request_timeouts": 2,
        "latest_error": None,
    }


def test_summarize_state_handles_non_dict_followers() -> None:
    assert bridge_state.summarize_state({"followers": "bad", "events": []}) == {
        "follower_count": 0,
        "event_count": 0,
        "total_in_flight": 0,
        "total_reconnect_attempts": 0,
        "total_request_timeouts": 0,
        "latest_error": None,
    }


def test_concurrent_snapshots_with_reused_pid_dont_collide(tmp_path: Path) -> None:
    """Two writers that share a PID (e.g. OS recycled a dead follower's PID)
    must both succeed and the on-disk state must reflect one of them, not
    a corrupted half-write or an OSError from racing the same tmp path."""
    import threading

    path = tmp_path / "bridge-state.json"
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(session_tag: str) -> None:
        try:
            barrier.wait(timeout=2.0)
            for _ in range(10):
                bridge_state.record_snapshot(
                    path=path,
                    follower_pid=4242,
                    remote_url=f"http://127.0.0.1/{session_tag}/mcp/",
                    remote_session_id=session_tag,
                    last_error=None,
                    in_flight=0,
                    reconnect_attempts=0,
                    request_timeouts=0,
                )
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("session-a",))
    t2 = threading.Thread(target=worker, args=("session-b",))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not errors, f"concurrent writers raised: {errors!r}"
    assert path.exists()
    data = json.loads(path.read_text())
    final_session = data["followers"]["4242"]["remote_session_id"]
    assert final_session in {"session-a", "session-b"}


def test_record_snapshot_leaves_no_tmp_files(tmp_path: Path) -> None:
    """The atomic replace must consume the staging file — no `*.tmp` leak."""
    path = tmp_path / "bridge-state.json"
    for i in range(5):
        bridge_state.record_snapshot(
            path=path,
            follower_pid=1000 + i,
            remote_url="http://127.0.0.1/mcp/",
            remote_session_id=f"sid-{i}",
            last_error=None,
            in_flight=0,
            reconnect_attempts=0,
            request_timeouts=0,
        )
    leftovers = sorted(tmp_path.glob("*.tmp"))
    assert leftovers == []
