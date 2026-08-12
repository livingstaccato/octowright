# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pruning manifest entries stranded by a dead daemon generation.

``remove_session`` only runs on a graceful close, so every entry open when a
daemon is SIGKILLed (``octowright restart``, a crash, an OOM kill) is stranded
forever — nothing reaps them. Observed live: 16 entries, of which 10 belonged
to five dead daemons, one of them a pid an ``octowright restart`` had killed
that same day.

"Orphaned" is decided by the recorded ``daemon_pid``, NOT by absence from the
live pool. At leader boot the pool is empty, so pool-absence alone would flag
every entry including ones a concurrently-live daemon owns. The pid test is
also deliberately conservative in the safe direction: if the recorded pid is
still alive the entry is KEPT, so a recycled pid can at worst leave a stale
entry, never delete a live one.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

from octowright import session_manifest as sm


def test_windows_manifest_lock_locks_one_byte_and_unlocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "manifest.json"
    calls: list[tuple[int, int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_NBLCK=3,
        LK_UNLCK=2,
        locking=lambda fd, mode, size: calls.append((fd, mode, size)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(sm.sys, "platform", "win32")

    with sm._manifest_lock(path):
        assert calls[-1][1:] == (fake_msvcrt.LK_NBLCK, 1)

    assert [mode for _fd, mode, _size in calls] == [fake_msvcrt.LK_NBLCK, fake_msvcrt.LK_UNLCK]
    assert all(size == 1 for _fd, _mode, size in calls)
    assert path.with_suffix(".json.lock").read_bytes()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_non_contention_manifest_lock_error_fails_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken filesystem is not contention and must not burn the timeout."""
    import fcntl

    def _broken_flock(*_args: object) -> None:
        raise OSError(errno.EIO, "filesystem failure")

    monkeypatch.setattr(fcntl, "flock", _broken_flock)
    monkeypatch.setattr(sm, "MANIFEST_LOCK_TIMEOUT_SECONDS", 0.2)
    started = time.monotonic()
    with pytest.raises(OSError, match="filesystem failure"), sm._manifest_lock(tmp_path / "manifest.json"):
        pass
    assert time.monotonic() - started < 0.1


def test_manifest_thread_lock_registry_releases_transient_paths(tmp_path: Path) -> None:
    """Per-path thread locks must not leak every tenant/path ever observed."""
    before = set(sm._THREAD_LOCKS)
    for index in range(20):
        with sm._manifest_lock(tmp_path / f"manifest-{index}.json"):
            pass
    assert set(sm._THREAD_LOCKS) == before


@pytest.mark.asyncio
async def test_manifest_record_callsite_keeps_event_loop_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A contended manifest write must run off the leader's asyncio thread."""
    from octowright.browser_pool import launch_helpers

    entered = threading.Event()
    release = threading.Event()

    def _blocking_record(**_kwargs: object) -> None:
        entered.set()
        release.wait(timeout=1.0)

    monkeypatch.setattr(launch_helpers, "_manifest_record_launch", _blocking_record)
    ticker = asyncio.Event()
    asyncio.get_running_loop().call_later(0.01, ticker.set)
    task = asyncio.create_task(
        launch_helpers._safe_manifest_record(
            instance_id="live",
            kind="chromium",
            label=None,
            profile=None,
            user_data_dir=None,
            log_path=tmp_path / "live.jsonl",
        )
    )
    try:
        await asyncio.wait_for(ticker.wait(), timeout=0.1)
        assert entered.wait(timeout=0.1)
        assert not task.done()
    finally:
        release.set()
    await task


@pytest.mark.asyncio
async def test_async_manifest_transaction_finishes_worker_before_cancellation() -> None:
    """Cancellation cannot leave a late manifest write racing its cleanup."""
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def _transaction() -> None:
        entered.set()
        release.wait(timeout=1.0)
        finished.set()

    task = asyncio.create_task(sm.run_manifest_transaction_async(_transaction))
    assert await asyncio.to_thread(entered.wait, 0.5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_async_manifest_transaction_finishes_worker_after_repeated_cancellation() -> None:
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def _transaction() -> None:
        entered.set()
        release.wait(timeout=1.0)
        finished.set()

    task = asyncio.create_task(sm.run_manifest_transaction_async(_transaction))
    assert await asyncio.to_thread(entered.wait, 0.5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.anyio
async def test_async_manifest_transaction_resists_anyio_level_cancellation() -> None:
    """Persistent cancel scopes cannot interrupt the post-cancel worker wait."""
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    scope_ready = anyio.Event()
    returned = anyio.Event()
    holder: dict[str, anyio.CancelScope] = {}

    def _transaction() -> None:
        entered.set()
        release.wait(timeout=1.0)
        finished.set()

    async def _runner() -> None:
        with anyio.CancelScope() as scope:
            holder["scope"] = scope
            scope_ready.set()
            await sm.run_manifest_transaction_async(_transaction)
        returned.set()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(_runner)
        await scope_ready.wait()
        assert await asyncio.to_thread(entered.wait, 0.5)
        holder["scope"].cancel()
        await anyio.sleep(0.01)
        assert not returned.is_set()
        release.set()
        await returned.wait()

    assert finished.is_set()


def _entry(session_id: str, daemon_pid: int) -> dict[str, object]:
    return {
        "session_id": session_id,
        "kind": "chromium",
        "label": None,
        "profile": None,
        "user_data_dir": None,
        "log_path": f"/tmp/{session_id}.jsonl",
        "launched_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
        "state": "open",
        "daemon_pid": daemon_pid,
    }


def _write(path: Path, entries: dict[str, dict[str, object]]) -> None:
    path.write_text(json.dumps({"schema_version": 1, "sessions": entries}), encoding="utf-8")


def _dead_pid() -> int:
    """A pid that is provably not running.

    Probes through ``singleton.pid_is_alive`` rather than ``os.kill(pid, 0)``
    directly: on Windows a dead pid raises ``OSError`` (WinError 87) instead of
    ``ProcessLookupError``, so a raw probe both crashes this helper and would
    mask the very bug these tests exist to catch.
    """
    from octowright.singleton import pid_is_alive

    for candidate in range(4_194_300, 4_194_000, -1):
        if not pid_is_alive(candidate):
            return candidate
    pytest.skip("could not find a provably-dead pid")
    raise AssertionError  # unreachable


def test_prunes_entries_from_a_dead_daemon(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    dead = _dead_pid()
    _write(path, {"a": _entry("a", dead), "b": _entry("b", dead)})

    removed = sm.prune_dead_daemon_entries(path=path)

    assert sorted(removed) == ["a", "b"]
    assert sm.read_manifest(path)["sessions"] == {}


def test_keeps_entries_owned_by_the_current_process(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    _write(path, {"mine": _entry("mine", os.getpid())})

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert "mine" in sm.read_manifest(path)["sessions"]


def test_keeps_entries_whose_daemon_is_still_alive(tmp_path: Path) -> None:
    # A concurrently-live daemon (e.g. --no-singleton sharing this manifest path)
    # must not have its entries pruned by another process booting.
    path = tmp_path / "manifest.json"
    _write(path, {"other": _entry("other", os.getpid())})

    assert sm.prune_dead_daemon_entries(current_pid=os.getpid() + 1, path=path) == []
    assert "other" in sm.read_manifest(path)["sessions"]


def test_mixed_manifest_prunes_only_the_dead(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    dead = _dead_pid()
    _write(path, {"live": _entry("live", os.getpid()), "stale": _entry("stale", dead)})

    assert sm.prune_dead_daemon_entries(path=path) == ["stale"]
    assert list(sm.read_manifest(path)["sessions"]) == ["live"]


def test_keeps_entries_with_no_recorded_pid(tmp_path: Path) -> None:
    # Pre-schema entries carry no daemon_pid; without proof they are dead, keep
    # them rather than guess (the conservative direction).
    path = tmp_path / "manifest.json"
    entry = _entry("legacy", 1)
    del entry["daemon_pid"]
    _write(path, {"legacy": entry})

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert "legacy" in sm.read_manifest(path)["sessions"]


def test_no_write_when_nothing_to_prune(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    _write(path, {"mine": _entry("mine", os.getpid())})
    before = path.stat().st_mtime_ns

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert path.stat().st_mtime_ns == before, "manifest rewritten despite no changes"


def test_missing_manifest_is_a_noop(tmp_path: Path) -> None:
    assert sm.prune_dead_daemon_entries(path=tmp_path / "absent.json") == []


def test_liveness_probe_routes_through_the_canonical_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the Windows break: an ad-hoc ``os.kill(pid, 0)`` probe reports a
    dead pid as ALIVE on Windows (it raises OSError/WinError 87, not
    ProcessLookupError), so nothing would ever be pruned there. Asserting we go
    through ``singleton.pid_is_alive`` catches that on any platform."""
    import octowright.singleton as singleton_mod

    calls: list[int] = []

    def fake_pid_is_alive(pid: int) -> bool:
        calls.append(pid)
        return False

    monkeypatch.setattr(singleton_mod, "pid_is_alive", fake_pid_is_alive)
    path = tmp_path / "manifest.json"
    _write(path, {"s": _entry("s", 424242)})

    assert sm.prune_dead_daemon_entries(path=path) == ["s"]
    assert calls == [424242], "liveness was not probed via singleton.pid_is_alive"


def test_unprobeable_pid_is_treated_as_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If liveness can't be determined, keep the entry — a stale entry is
    harmless, deleting a live one is not."""
    import octowright.singleton as singleton_mod

    def boom(_pid: int) -> bool:
        raise OSError("cannot probe")

    monkeypatch.setattr(singleton_mod, "pid_is_alive", boom)
    path = tmp_path / "manifest.json"
    _write(path, {"s": _entry("s", 424242)})

    assert sm.prune_dead_daemon_entries(path=path) == []
    assert "s" in sm.read_manifest(path)["sessions"]


def test_record_launch_waits_for_manifest_transaction_lock(tmp_path: Path) -> None:
    """A concurrent writer must not enter while another RMW owns the lock."""
    path = tmp_path / "manifest.json"
    started = threading.Event()
    finished = threading.Event()

    def _record() -> None:
        started.set()
        sm.record_launch(
            session_id="new-live",
            kind="chromium",
            label=None,
            profile=None,
            user_data_dir=None,
            log_path=tmp_path / "new-live.jsonl",
            path=path,
        )
        finished.set()

    with sm._manifest_lock(path):
        writer = threading.Thread(target=_record)
        writer.start()
        assert started.wait(1)
        assert not finished.wait(0.05), "writer bypassed the manifest transaction lock"
        assert not path.exists()

    writer.join(timeout=2)
    assert not writer.is_alive()
    assert finished.is_set()
    assert "new-live" in sm.read_manifest(path)["sessions"]


def test_every_manifest_rmw_uses_the_transaction_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Launch, close, and dead-daemon prune must share one lock discipline."""
    path = tmp_path / "manifest.json"
    acquired: list[Path] = []

    @contextmanager
    def _tracked_lock(lock_path: Path) -> Iterator[None]:
        acquired.append(lock_path)
        yield

    monkeypatch.setattr(sm, "_manifest_lock", _tracked_lock)
    monkeypatch.setattr(sm, "_pid_alive", lambda _pid: False)

    sm.record_launch(
        session_id="live",
        kind="chromium",
        label=None,
        profile=None,
        user_data_dir=None,
        log_path=tmp_path / "live.jsonl",
        path=path,
    )
    assert sm.rekey_session("live", "rekeyed", path=path)
    assert sm.remove_session("rekeyed", path=path)
    _write(path, {"stale": _entry("stale", 424242)})
    assert sm.prune_dead_daemon_entries(path=path) == ["stale"]

    assert acquired == [path, path, path, path]


def test_rekey_session_replaces_stale_target_with_live_source(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    stale = _entry("old", 111)
    source = _entry("new", 222)
    source["log_path"] = "/tmp/replacement.jsonl"
    _write(path, {"old": stale, "new": source})

    assert sm.rekey_session("new", "old", path=path)

    sessions = sm.read_manifest(path)["sessions"]
    assert list(sessions) == ["old"]
    assert sessions["old"]["session_id"] == "old"
    assert sessions["old"]["daemon_pid"] == 222
    assert sessions["old"]["log_path"] == "/tmp/replacement.jsonl"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_contended_manifest_writer_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A timed-out OS lock must leave the prior manifest byte-for-byte intact."""
    import fcntl

    path = tmp_path / "manifest.json"
    _write(path, {"live": _entry("live", os.getpid())})
    before = path.read_bytes()
    lock_path = path.with_suffix(path.suffix + ".lock")
    monkeypatch.setattr(sm, "MANIFEST_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(sm, "MANIFEST_LOCK_POLL_SECONDS", 0.005)

    with lock_path.open("a+b") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        with pytest.raises(TimeoutError, match="manifest lock"):
            sm.record_launch(
                session_id="must-not-appear",
                kind="chromium",
                label=None,
                profile=None,
                user_data_dir=None,
                log_path=tmp_path / "must-not-appear.jsonl",
                path=path,
            )

    assert path.read_bytes() == before
