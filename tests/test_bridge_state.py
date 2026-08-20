# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import errno
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from octowright import bridge_state
from octowright.version import VERSION


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_permanent_state_lock_error_fails_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl

    monkeypatch.setattr(bridge_state, "STATE_LOCK_TIMEOUT_SECONDS", 0.2)

    def _unsupported(*_args: object) -> None:
        raise OSError(errno.ENOTSUP, "locking unsupported")

    monkeypatch.setattr(fcntl, "flock", _unsupported)
    started = time.monotonic()
    with bridge_state._state_lock(tmp_path / "state.json") as acquired:
        assert acquired is False
    assert time.monotonic() - started < 0.1


@pytest.mark.asyncio
async def test_async_state_transactions_keep_event_loop_responsive(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()

    def _blocking_record(**_kwargs: object) -> None:
        entered.set()
        release.wait(timeout=1.0)

    monkeypatch.setattr(bridge_state, "record_snapshot", _blocking_record)
    transaction = asyncio.create_task(bridge_state.record_snapshot_async(path=Path("ignored")))
    assert await asyncio.to_thread(entered.wait, 0.5)
    ticker = asyncio.create_task(asyncio.sleep(0.01))
    await asyncio.wait_for(ticker, timeout=0.1)
    assert not transaction.done()
    release.set()
    await transaction


def test_windows_state_lock_locks_one_byte_and_unlocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bridge-state.json"
    calls: list[tuple[int, int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=1,
        LK_NBLCK=3,
        LK_UNLCK=2,
        locking=lambda fd, mode, size: calls.append((fd, mode, size)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(bridge_state.sys, "platform", "win32")

    with bridge_state._state_lock(path) as acquired:
        assert acquired is True
        assert calls[-1][1:] == (fake_msvcrt.LK_NBLCK, 1)

    assert [mode for _fd, mode, _size in calls] == [fake_msvcrt.LK_NBLCK, fake_msvcrt.LK_UNLCK]
    assert all(size == 1 for _fd, _mode, size in calls)
    assert path.with_suffix(".json.lock").read_bytes()


def test_windows_state_lock_unlocks_when_transaction_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bridge-state.json"
    modes: list[int] = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=1,
        LK_NBLCK=3,
        LK_UNLCK=2,
        locking=lambda _fd, mode, _size: modes.append(mode),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(bridge_state.sys, "platform", "win32")

    with pytest.raises(RuntimeError, match="transaction failed"), bridge_state._state_lock(path) as acquired:
        assert acquired is True
        raise RuntimeError("transaction failed")

    assert modes == [fake_msvcrt.LK_NBLCK, fake_msvcrt.LK_UNLCK]


def test_same_process_state_lock_wait_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bridge-state.json"
    lock_path = path.with_suffix(path.suffix + ".lock")
    monkeypatch.setattr(bridge_state, "STATE_LOCK_TIMEOUT_SECONDS", 0.05)
    held = threading.Event()
    release = threading.Event()

    def _holder() -> None:
        with bridge_state._thread_state_lock(lock_path):
            held.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=_holder)
    thread.start()
    assert held.wait(1)
    try:
        started = time.monotonic()
        with bridge_state._state_lock(path) as acquired:
            assert acquired is False
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


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


def test_record_snapshot_skips_when_lock_file_open_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=path,
        follower_pid=123,
        remote_url="http://127.0.0.1:8765/mcp/",
        remote_session_id="before-open-failure",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    before = path.read_text()

    def _fail_lock_open(*_args, **_kwargs):
        raise OSError("lock directory unavailable")

    monkeypatch.setattr(bridge_state, "open", _fail_lock_open, raising=False)
    bridge_state.record_snapshot(
        path=path,
        follower_pid=123,
        remote_url="http://127.0.0.1:8765/mcp/",
        remote_session_id="must-be-skipped",
        last_error="lock open failed",
        in_flight=1,
        reconnect_attempts=1,
        request_timeouts=1,
    )

    assert path.read_text() == before


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
        "leader_version": VERSION,
        # Neither snapshot reports a version, so both predate the field.
        "follower_versions": {bridge_state.UNKNOWN_FOLLOWER_VERSION: 2},
        "stale_follower_count": 2,
        "stale_follower_hint": bridge_state._STALE_FOLLOWER_HINT,
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
        "leader_version": VERSION,
        "follower_versions": {bridge_state.UNKNOWN_FOLLOWER_VERSION: 2},
        "stale_follower_count": 2,
        "stale_follower_hint": bridge_state._STALE_FOLLOWER_HINT,
    }


def test_summarize_state_handles_non_dict_followers() -> None:
    assert bridge_state.summarize_state({"followers": "bad", "events": []}) == {
        "follower_count": 0,
        "event_count": 0,
        "total_in_flight": 0,
        "total_reconnect_attempts": 0,
        "total_request_timeouts": 0,
        "latest_error": None,
        "leader_version": VERSION,
        "follower_versions": {},
        "stale_follower_count": 0,
        "stale_follower_hint": None,
    }


def test_a_follower_records_its_own_version(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=path,
        follower_pid=os.getpid(),
        remote_url="http://127.0.0.1:6286/mcp/",
        remote_session_id=None,
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )

    snapshot = bridge_state.read_state(path)["followers"][str(os.getpid())]

    assert snapshot["follower_version"] == VERSION


def test_version_skew_is_reported_rather_than_left_to_forensics() -> None:
    """A follower is a subprocess its MCP CLIENT owns; it survives a leader
    restart by design, so a daemon restart cannot deploy follower-side code.
    The self-identifying header carries a pid and nothing else, so working out
    which followers were stale meant reading process start times against commit
    timestamps by hand."""
    data = {
        "followers": {
            "1": {"ts": 1.0, "follower_version": VERSION},
            "2": {"ts": 2.0, "follower_version": "0.14.4"},
            "3": {"ts": 3.0, "follower_version": "0.14.4"},
            "4": {"ts": 4.0},  # predates the field entirely
        },
        "events": [],
    }

    summary = bridge_state.summarize_state(data)

    assert summary["leader_version"] == VERSION
    assert summary["follower_versions"] == {"0.14.4": 2, VERSION: 1, bridge_state.UNKNOWN_FOLLOWER_VERSION: 1}
    assert summary["stale_follower_count"] == 3
    assert summary["follower_count"] == 4


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


def test_remove_followers_skips_when_lock_file_open_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def _fail_lock_open(*_args, **_kwargs):
        raise OSError("lock directory unavailable")

    monkeypatch.setattr(bridge_state, "open", _fail_lock_open, raising=False)
    bridge_state.remove_followers(path, [111])

    assert path.read_text() == before


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


def test_concurrent_record_snapshot_keeps_both_followers(tmp_path: Path, monkeypatch) -> None:
    """Two followers writing concurrently must BOTH survive: the read-modify-
    replace is serialized by an exclusive file lock. Without it, both read the
    same pre-state and the second write erases the first registration (the
    dead-follower reaper then never learns about that follower).

    The barrier forces the interleaving: both threads pass read_state before
    either writes. Only cross-process/thread locking makes this pass."""
    import contextlib
    import threading

    path = tmp_path / "bridge-state.json"
    # Short-timeout barrier: WITHOUT a lock both writers reach it concurrently,
    # pass instantly and both write the same pre-state (lost update). WITH the
    # lock the second writer is blocked before its read, so the first's barrier
    # simply times out (suppressed) and the writes serialize correctly.
    barrier = threading.Barrier(2, timeout=0.5)
    orig_read = bridge_state.read_state

    def _barriered_read(p: Path):
        state = orig_read(p)
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait()
        return state

    monkeypatch.setattr(bridge_state, "read_state", _barriered_read)
    # keep_pid protects each writer's own entry; other pids must look alive.
    monkeypatch.setattr(bridge_state, "_pid_alive", lambda _pid: True)

    def _write(pid: int) -> None:
        bridge_state.record_snapshot(
            path=path,
            follower_pid=pid,
            remote_url=f"http://127.0.0.1:8765/mcp/{pid}",
            remote_session_id=f"sid-{pid}",
            last_error=None,
            in_flight=0,
            reconnect_attempts=0,
            request_timeouts=0,
        )

    t1 = threading.Thread(target=_write, args=(111,))
    t2 = threading.Thread(target=_write, args=(222,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    data = json.loads(path.read_text())
    assert set(data["followers"]) == {"111", "222"}
