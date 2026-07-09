# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import os
import time
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


def test_remove_followers_drops_specified_pids(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=path,
        follower_pid=111,
        remote_url="http://a/mcp/",
        remote_session_id="sid-a",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    bridge_state.record_snapshot(
        path=path,
        follower_pid=222,
        remote_url="http://b/mcp/",
        remote_session_id="sid-b",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )

    bridge_state.remove_followers(path, [111])

    data = json.loads(path.read_text())
    assert "111" not in data["followers"]
    assert "222" in data["followers"]


def test_remove_followers_noop_when_no_match(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=path,
        follower_pid=111,
        remote_url="http://a/mcp/",
        remote_session_id="sid-a",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    before = path.read_text()

    bridge_state.remove_followers(path, [999])  # no such entry

    assert path.read_text() == before


def test_remove_followers_empty_pids_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"  # doesn't even exist yet
    bridge_state.remove_followers(path, [])
    assert not path.exists()


def test_remove_followers_leaves_no_tmp_files(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=path,
        follower_pid=111,
        remote_url="http://a/mcp/",
        remote_session_id="sid-a",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    bridge_state.remove_followers(path, [111])
    assert sorted(tmp_path.glob("*.tmp")) == []


def test_remove_followers_swallows_write_oserror(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=path,
        follower_pid=111,
        remote_url="http://a/mcp/",
        remote_session_id="sid-a",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )

    def _boom(self, *_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)

    bridge_state.remove_followers(path, [111])  # must not raise


def test_record_snapshot_prunes_dead_followers(tmp_path: Path) -> None:
    """A follower recording its own snapshot prunes OTHER followers whose PID is
    dead, so the registry tracks live followers instead of growing unbounded
    (the leak that grew bridge-state to 641 stale entries / 167 KB)."""
    import os

    path = tmp_path / "bridge-state.json"
    dead_pid = 1_000_000_000  # no such process -> ProcessLookupError on os.kill

    bridge_state.record_snapshot(
        path=path,
        follower_pid=dead_pid,
        remote_url="http://x/mcp/",
        remote_session_id="stale",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    bridge_state.record_snapshot(
        path=path,
        follower_pid=os.getpid(),
        remote_url="http://y/mcp/",
        remote_session_id="live",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )

    data = json.loads(path.read_text())
    assert str(os.getpid()) in data["followers"]  # live follower kept
    assert str(dead_pid) not in data["followers"]  # stale follower pruned


def test_sweep_stale_tmp_files_removes_only_old_ones(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    old_tmp = tmp_path / "bridge-state.json.111.1.tmp"
    fresh_tmp = tmp_path / "bridge-state.json.222.2.tmp"
    old_tmp.write_text("{}")
    fresh_tmp.write_text("{}")

    old_time = time.time() - 1000.0
    os.utime(old_tmp, (old_time, old_time))
    # fresh_tmp keeps its just-written mtime — inside the age window.

    removed = bridge_state.sweep_stale_tmp_files(path, max_age_seconds=300.0)

    assert removed == [old_tmp.name]
    assert not old_tmp.exists()
    assert fresh_tmp.exists()


def test_sweep_stale_tmp_files_ignores_unrelated_files(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    unrelated = tmp_path / "some-other-file.tmp"
    unrelated.write_text("{}")
    old_time = time.time() - 1000.0
    os.utime(unrelated, (old_time, old_time))

    removed = bridge_state.sweep_stale_tmp_files(path, max_age_seconds=300.0)

    assert removed == []
    assert unrelated.exists()


def test_sweep_stale_tmp_files_missing_dir_is_noop() -> None:
    removed = bridge_state.sweep_stale_tmp_files(Path("/nonexistent-dir-xyz/bridge-state.json"))
    assert removed == []


def test_sweep_stale_tmp_files_swallows_glob_oserror(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "bridge-state.json"

    def _boom(self, _pattern):
        raise OSError("glob blew up")

    monkeypatch.setattr(Path, "glob", _boom)
    removed = bridge_state.sweep_stale_tmp_files(path)  # must not raise
    assert removed == []


def test_sweep_stale_tmp_files_swallows_unlink_race(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "bridge-state.json"
    tmp = tmp_path / "bridge-state.json.111.1.tmp"
    tmp.write_text("{}")
    old_time = time.time() - 1000.0
    os.utime(tmp, (old_time, old_time))

    real_unlink = Path.unlink

    def _boom(self, *a, **kw):
        if self == tmp:
            raise OSError("raced by another process")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", _boom)

    removed = bridge_state.sweep_stale_tmp_files(path, max_age_seconds=300.0)  # must not raise
    assert removed == []
