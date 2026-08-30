# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader housekeeping: orphan reap pass + in-place daemon-log truncation."""

from __future__ import annotations

import asyncio
import json
import os
import stat as _stat
from unittest.mock import MagicMock

import pytest

from octowright import housekeeping


@pytest.fixture(autouse=True)
def _isolate_session_manifest(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Boot-cleanup tests must never prune the developer's real manifest."""
    from octowright import session_manifest

    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "session-manifest.json")


def _reaper_returning(monkeypatch: pytest.MonkeyPatch, summary: dict) -> None:
    from octowright import process_reaper

    monkeypatch.setattr(process_reaper, "reap_orphan_browsers", lambda **_kw: summary)


def test_reap_at_boot_logs_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    _reaper_returning(monkeypatch, {"killed": [10, 11], "still_alive": [], "errors": []})
    log = MagicMock()
    housekeeping.reap_orphan_browsers_at_boot(log=log)
    log.warning.assert_called_once()
    assert log.warning.call_args.kwargs["count"] == 2


def test_reap_at_boot_silent_when_nothing_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    _reaper_returning(monkeypatch, {"killed": [], "still_alive": [], "errors": []})
    log = MagicMock()
    housekeeping.reap_orphan_browsers_at_boot(log=log)
    log.warning.assert_not_called()


def test_reap_at_boot_swallows_reaper_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import process_reaper

    def _boom(**_kw: object) -> dict:
        raise RuntimeError("ps blew up")

    monkeypatch.setattr(process_reaper, "reap_orphan_browsers", _boom)
    prune = MagicMock()
    monkeypatch.setattr(housekeeping, "_prune_dead_daemon_manifest_entries", prune)
    log = MagicMock()
    housekeeping.reap_orphan_browsers_at_boot(log=log)  # must not raise
    log.warning.assert_called_once()
    assert log.warning.call_args.args[0] == "octowright.boot.orphan_reap_failed"
    prune.assert_called_once_with(log=log)


def test_reap_at_boot_keeps_manifest_diagnostic_for_surviving_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reaper_returning(
        monkeypatch,
        {
            "killed": [],
            "still_alive": [2000],
            "errors": [{"pid": "2000", "stage": "sigkill", "error": "denied"}],
        },
    )
    prune = MagicMock()
    monkeypatch.setattr(housekeeping, "_prune_dead_daemon_manifest_entries", prune)
    log = MagicMock()

    housekeeping.reap_orphan_browsers_at_boot(log=log)

    prune.assert_not_called()
    log.warning.assert_called_once_with(
        "octowright.boot.orphan_reap_incomplete",
        still_alive=[2000],
        errors=[{"pid": "2000", "stage": "sigkill", "error": "denied"}],
    )


def test_start_housekeeping_task_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults

    monkeypatch.setattr(defaults, "HOUSEKEEPING_INTERVAL_SECONDS", None)
    assert housekeeping.start_housekeeping_task(MagicMock()) is None


def test_start_housekeeping_task_enabled_creates_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults

    monkeypatch.setattr(defaults, "HOUSEKEEPING_INTERVAL_SECONDS", 60.0)

    async def _run() -> None:
        task = housekeeping.start_housekeeping_task(MagicMock())
        assert task is not None and not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_reap_orphans_once_logs_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import process_reaper

    monkeypatch.setattr(
        process_reaper,
        "reap_orphan_browsers",
        lambda **_kw: {"killed": [2000, 2001], "still_alive": [], "errors": []},
    )
    log = MagicMock()
    housekeeping._reap_orphans_once(log=log)
    log.warning.assert_called_once()
    assert log.warning.call_args.kwargs["count"] == 2


def test_reap_orphans_once_silent_when_nothing_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import process_reaper

    monkeypatch.setattr(
        process_reaper,
        "reap_orphan_browsers",
        lambda **_kw: {"killed": [], "still_alive": [], "errors": []},
    )
    log = MagicMock()
    housekeeping._reap_orphans_once(log=log)
    log.warning.assert_not_called()


def test_log_guard_skips_when_stderr_not_regular_file(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pretend fd 2 is a character device (a terminal) — must not truncate.
    fake_stat = os.stat_result((_stat.S_IFCHR | 0o600, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(housekeeping.os, "fstat", lambda _fd: fake_stat)
    ftruncate = MagicMock()
    monkeypatch.setattr(housekeeping.os, "ftruncate", ftruncate)
    housekeeping._guard_daemon_log_size(log=MagicMock())
    ftruncate.assert_not_called()


def test_log_guard_skips_under_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import daemonize

    small = os.stat_result((_stat.S_IFREG | 0o600, 0, 0, 1, 0, 0, daemonize._DAEMON_LOG_MAX_BYTES - 1, 0, 0, 0))
    monkeypatch.setattr(housekeeping.os, "fstat", lambda _fd: small)
    ftruncate = MagicMock()
    monkeypatch.setattr(housekeeping.os, "ftruncate", ftruncate)
    housekeeping._guard_daemon_log_size(log=MagicMock())
    ftruncate.assert_not_called()


def test_log_guard_truncates_when_over_cap_and_same_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import daemonize

    big = os.stat_result((_stat.S_IFREG | 0o600, 42, 7, 1, 0, 0, daemonize._DAEMON_LOG_MAX_BYTES + 1, 0, 0, 0))
    monkeypatch.setattr(housekeeping.os, "fstat", lambda _fd: big)
    # Make os.stat(_DAEMON_LOG) report the same dev/ino so samestat() matches.
    monkeypatch.setattr(housekeeping.os, "stat", lambda _p: big)
    calls: dict[str, object] = {}
    monkeypatch.setattr(housekeeping.os, "ftruncate", lambda fd, size: calls.update(ftruncate=(fd, size)))
    monkeypatch.setattr(housekeeping.os, "write", lambda fd, data: calls.update(write=(fd, data)) or len(data))
    log = MagicMock()
    housekeeping._guard_daemon_log_size(log=log)
    assert calls["ftruncate"] == (2, 0)
    assert calls["write"][0] == 2
    log.info.assert_called_once()


def test_log_guard_skips_when_fd_is_different_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import daemonize

    fd_stat = os.stat_result((_stat.S_IFREG | 0o600, 42, 7, 1, 0, 0, daemonize._DAEMON_LOG_MAX_BYTES + 1, 0, 0, 0))
    other = os.stat_result((_stat.S_IFREG | 0o600, 99, 99, 1, 0, 0, 10, 0, 0, 0))
    monkeypatch.setattr(housekeeping.os, "fstat", lambda _fd: fd_stat)
    monkeypatch.setattr(housekeeping.os, "stat", lambda _p: other)
    ftruncate = MagicMock()
    monkeypatch.setattr(housekeeping.os, "ftruncate", ftruncate)
    housekeeping._guard_daemon_log_size(log=MagicMock())
    ftruncate.assert_not_called()


def _raise_oserror(*_a: object, **_kw: object) -> object:
    raise OSError("boom")


def test_log_guard_returns_when_fstat_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # fd 2 not stat-able → bail without crashing.
    monkeypatch.setattr(housekeeping.os, "fstat", _raise_oserror)
    ftruncate = MagicMock()
    monkeypatch.setattr(housekeeping.os, "ftruncate", ftruncate)
    housekeeping._guard_daemon_log_size(log=MagicMock())
    ftruncate.assert_not_called()


def test_log_guard_returns_when_path_stat_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import daemonize

    big = os.stat_result((_stat.S_IFREG | 0o600, 42, 7, 1, 0, 0, daemonize._DAEMON_LOG_MAX_BYTES + 1, 0, 0, 0))
    monkeypatch.setattr(housekeeping.os, "fstat", lambda _fd: big)
    monkeypatch.setattr(housekeeping.os, "stat", _raise_oserror)  # _DAEMON_LOG vanished mid-check
    ftruncate = MagicMock()
    monkeypatch.setattr(housekeeping.os, "ftruncate", ftruncate)
    housekeeping._guard_daemon_log_size(log=MagicMock())
    ftruncate.assert_not_called()


def test_daemon_housekeeping_loop_runs_jobs_and_survives_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    # The loop must call both jobs each tick and keep going when either raises,
    # logging the failure rather than dying.
    calls = {"reap": 0, "guard": 0}

    def _reap(*, log: object) -> None:
        calls["reap"] += 1
        raise RuntimeError("reap boom")

    def _guard(*, log: object) -> None:
        calls["guard"] += 1
        raise RuntimeError("guard boom")

    monkeypatch.setattr(housekeeping, "_reap_orphans_once", _reap)
    monkeypatch.setattr(housekeeping, "_guard_daemon_log_size", _guard)
    log = MagicMock()

    async def _run() -> None:
        task = asyncio.create_task(housekeeping.daemon_housekeeping(interval_seconds=0.001, log=log))
        for _ in range(200):
            await asyncio.sleep(0.001)
            if calls["reap"] >= 1 and calls["guard"] >= 1:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert calls["reap"] >= 1 and calls["guard"] >= 1
    # Both per-job failures were logged; the loop didn't crash.
    logged = {c.args[0] for c in log.warning.call_args_list}
    assert "octowright.housekeeping.reap_failed" in logged
    assert "octowright.housekeeping.log_guard_failed" in logged


def _fake_transport(*, terminated: bool = False):
    from unittest.mock import AsyncMock

    transport = MagicMock()
    transport.is_terminated = terminated
    transport.terminate = AsyncMock()
    return transport


def _install_fake_session_manager(monkeypatch: pytest.MonkeyPatch, instances: dict):
    import octowright.server as server_mod

    fake_manager = MagicMock()
    fake_manager._server_instances = instances
    fake_mcp = MagicMock()
    fake_mcp.session_manager = fake_manager
    monkeypatch.setattr(server_mod, "mcp", fake_mcp)


def test_reap_dead_follower_sessions_terminates_and_prunes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    dead_pid = 1_000_000_000  # no such process
    bridge_state.record_snapshot(
        path=path,
        follower_pid=dead_pid,
        remote_url="http://x/mcp/",
        remote_session_id="dead-sid",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )

    transport = _fake_transport()
    instances = {"dead-sid": transport}
    _install_fake_session_manager(monkeypatch, instances)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))

    transport.terminate.assert_awaited_once()
    assert "dead-sid" not in instances
    data = json.loads(path.read_text())
    assert str(dead_pid) not in data["followers"]
    log.warning.assert_called_once()
    assert log.warning.call_args.kwargs["count"] == 1


def test_reap_dead_follower_sessions_leaves_live_pid_alone(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    bridge_state.record_snapshot(
        path=path,
        follower_pid=os.getpid(),
        remote_url="http://x/mcp/",
        remote_session_id="live-sid",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )

    transport = _fake_transport()
    instances = {"live-sid": transport}
    _install_fake_session_manager(monkeypatch, instances)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))

    transport.terminate.assert_not_awaited()
    assert "live-sid" in instances
    data = json.loads(path.read_text())
    assert str(os.getpid()) in data["followers"]
    log.warning.assert_not_called()


def test_reap_dead_follower_sessions_pops_already_terminated_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A transport some other path already terminated (e.g. the opt-in idle
    reaper) must still get its dict entry popped, or it leaks forever — the
    manager's own cleanup path skips popping once is_terminated is True."""
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    dead_pid = 1_000_000_001
    bridge_state.record_snapshot(
        path=path,
        follower_pid=dead_pid,
        remote_url="http://x/mcp/",
        remote_session_id="already-dead-sid",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )

    transport = _fake_transport(terminated=True)
    instances = {"already-dead-sid": transport}
    _install_fake_session_manager(monkeypatch, instances)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))

    transport.terminate.assert_not_awaited()  # already terminated — don't call again
    assert "already-dead-sid" not in instances  # but the dict entry is still freed


def test_reap_dead_follower_sessions_noop_when_not_leader(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """No session_manager (this process isn't the HTTP-MCP leader) -> quiet no-op."""
    import octowright.server as server_mod
    from octowright import defaults
    from octowright import housekeeping as _hk

    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", tmp_path / "bridge-state.json")
    fake_mcp = MagicMock()
    fake_mcp.session_manager = None
    monkeypatch.setattr(server_mod, "mcp", fake_mcp)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))  # must not raise
    log.warning.assert_not_called()


def test_reap_dead_follower_sessions_missing_followers_dict_noop(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", tmp_path / "bridge-state.json")
    monkeypatch.setattr(bridge_state, "read_state", lambda _path: {"followers": "not-a-dict", "events": []})
    instances: dict = {}
    _install_fake_session_manager(monkeypatch, instances)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))  # must not raise
    log.warning.assert_not_called()


def test_reap_dead_follower_sessions_skips_non_dict_snapshot_entry(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    monkeypatch.setattr(
        bridge_state, "read_state", lambda _path: {"followers": {"111": "not-a-snapshot-dict"}, "events": []}
    )
    instances: dict = {}
    _install_fake_session_manager(monkeypatch, instances)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))  # must not raise
    log.warning.assert_not_called()


def test_reap_dead_follower_sessions_skips_unparsable_pid_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    monkeypatch.setattr(
        bridge_state,
        "read_state",
        lambda _path: {"followers": {"not-a-pid": {"remote_session_id": "sid"}}, "events": []},
    )
    instances: dict = {}
    _install_fake_session_manager(monkeypatch, instances)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))  # must not raise
    log.warning.assert_not_called()


def test_reap_dead_follower_sessions_treats_liveness_check_error_as_dead(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """pid_is_alive raising (overflowing/malformed PID) must be treated as dead,
    not skipped — the try/except around it defaults conservatively to reap."""
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    bridge_state.record_snapshot(
        path=path,
        follower_pid=999,
        remote_url="http://x/mcp/",
        remote_session_id="sid-999",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    transport = _fake_transport()
    _install_fake_session_manager(monkeypatch, {"sid-999": transport})
    import octowright.singleton as singleton_mod

    def _raise_value_error(_pid: int) -> bool:
        raise ValueError("bad pid")

    monkeypatch.setattr(singleton_mod, "pid_is_alive", _raise_value_error)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))

    transport.terminate.assert_awaited_once()


def test_reap_dead_follower_sessions_dead_pid_with_no_session_id_still_prunes_bridge_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    dead_pid = 1_000_000_003
    bridge_state.record_snapshot(
        path=path,
        follower_pid=dead_pid,
        remote_url="http://x/mcp/",
        remote_session_id=None,
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    instances: dict = {}
    _install_fake_session_manager(monkeypatch, instances)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))

    data = json.loads(path.read_text())
    assert str(dead_pid) not in data["followers"]  # bridge-state cleaned up
    log.warning.assert_not_called()  # nothing to terminate — no session_id to act on


def test_reap_dead_follower_sessions_dead_pid_session_not_in_instances(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A dead pid whose session_id isn't (or is no longer) in the manager's
    instances dict must be skipped cleanly, not raise."""
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    dead_pid = 1_000_000_004
    bridge_state.record_snapshot(
        path=path,
        follower_pid=dead_pid,
        remote_url="http://x/mcp/",
        remote_session_id="gone-sid",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    instances: dict = {}  # "gone-sid" not present
    _install_fake_session_manager(monkeypatch, instances)

    log = MagicMock()
    asyncio.run(_hk._reap_dead_follower_sessions_once(log=log))  # must not raise

    data = json.loads(path.read_text())
    assert str(dead_pid) not in data["followers"]
    log.warning.assert_not_called()


def test_daemon_housekeeping_loop_survives_tmp_sweep_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping as _hk

    monkeypatch.setattr(_hk, "_reap_orphans_once", lambda **_kw: None)
    monkeypatch.setattr(_hk, "_guard_daemon_log_size", lambda **_kw: None)

    async def _noop_follower_reap(*, log: object) -> None:
        return None

    monkeypatch.setattr(_hk, "_reap_dead_follower_sessions_once", _noop_follower_reap)

    def _boom(*, log: object) -> None:
        raise RuntimeError("sweep boom")

    monkeypatch.setattr(_hk, "_sweep_bridge_state_tmp_once", _boom)
    log = MagicMock()

    async def _run() -> None:
        task = asyncio.create_task(_hk.daemon_housekeeping(interval_seconds=0.001, log=log))
        for _ in range(200):
            await asyncio.sleep(0.001)
            if log.warning.call_args_list:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    logged = {c.args[0] for c in log.warning.call_args_list}
    assert "octowright.housekeeping.bridge_tmp_sweep_failed" in logged


def test_reap_dead_follower_sessions_swallows_failure_in_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping as _hk

    async def _boom(*, log: object) -> None:
        raise RuntimeError("boom")

    calls = {"reap": 0, "guard": 0, "follower": 0}

    def _reap(*, log: object) -> None:
        calls["reap"] += 1

    def _guard(*, log: object) -> None:
        calls["guard"] += 1

    async def _follower_reap(*, log: object) -> None:
        calls["follower"] += 1
        raise RuntimeError("follower reap boom")

    monkeypatch.setattr(_hk, "_reap_orphans_once", _reap)
    monkeypatch.setattr(_hk, "_guard_daemon_log_size", _guard)
    monkeypatch.setattr(_hk, "_reap_dead_follower_sessions_once", _follower_reap)
    log = MagicMock()

    async def _run() -> None:
        task = asyncio.create_task(_hk.daemon_housekeeping(interval_seconds=0.001, log=log))
        for _ in range(200):
            await asyncio.sleep(0.001)
            if calls["follower"] >= 1:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert calls["follower"] >= 1
    logged = {c.args[0] for c in log.warning.call_args_list}
    assert "octowright.housekeeping.follower_reap_failed" in logged


def test_sample_process_rss_records_leader_browsers_total(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping as _hk
    from octowright import process_reaper, sysresources
    from tests._metric_recorders import RecordingHistogram

    rec = RecordingHistogram()
    monkeypatch.setattr(_hk, "_PROCESS_RSS", rec)
    monkeypatch.setattr(process_reaper, "find_browser_pids", lambda _scope, *, root_pid=None: [99, 100])
    # 1000 bytes for the leader ([self pid]), 500 for the browser pids.
    monkeypatch.setattr(sysresources, "process_rss_bytes", lambda pids: 1000 if pids == [os.getpid()] else 500)

    _hk._sample_process_rss()

    assert rec.values_for("scope", "leader") == [1000]
    assert rec.values_for("scope", "browsers") == [500]
    assert rec.values_for("scope", "total") == [1500]


def test_sample_process_rss_real_host_reports_leader() -> None:
    """Real RSS read for this process (no mocks): leader scope is positive and
    total == leader + browsers — exercises the ps-based reader end to end."""
    from octowright import housekeeping as _hk
    from tests._metric_recorders import RecordingHistogram

    rec = RecordingHistogram()
    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(_hk, "_PROCESS_RSS", rec)
    try:
        _hk._sample_process_rss()
    finally:
        mp.undo()

    leader = rec.values_for("scope", "leader")
    browsers = rec.values_for("scope", "browsers")
    total = rec.values_for("scope", "total")
    assert leader and leader[0] > 0  # our own process has real RSS
    assert total[0] == leader[0] + browsers[0]


def test_reap_dead_follower_sessions_increments_process_lifetime_counter(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    monkeypatch.setattr(_hk, "_reaped_follower_sessions_total", 0)
    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    dead_pid = 1_000_000_002
    bridge_state.record_snapshot(
        path=path,
        follower_pid=dead_pid,
        remote_url="http://x/mcp/",
        remote_session_id="dead-sid",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    _install_fake_session_manager(monkeypatch, {"dead-sid": _fake_transport()})

    asyncio.run(_hk._reap_dead_follower_sessions_once(log=MagicMock()))

    assert _hk.get_reaped_follower_session_count() == 1


def test_sweep_bridge_state_tmp_once_logs_when_removed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from octowright import bridge_state, defaults
    from octowright import housekeeping as _hk

    path = tmp_path / "bridge-state.json"
    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", path)
    monkeypatch.setattr(bridge_state, "sweep_stale_tmp_files", lambda _path, **_kw: ["a.tmp", "b.tmp"])

    log = MagicMock()
    _hk._sweep_bridge_state_tmp_once(log=log)

    log.warning.assert_called_once()
    assert log.warning.call_args.args[0] == "octowright.housekeeping.swept_stale_bridge_tmp_files"
    assert log.warning.call_args.kwargs["count"] == 2


def test_sweep_bridge_state_tmp_once_silent_when_nothing_removed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from octowright import defaults
    from octowright import housekeeping as _hk

    monkeypatch.setattr(defaults, "BRIDGE_STATE_PATH", tmp_path / "bridge-state.json")
    log = MagicMock()
    _hk._sweep_bridge_state_tmp_once(log=log)
    log.warning.assert_not_called()


def test_daemon_housekeeping_loop_runs_tmp_sweep_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping as _hk

    calls = {"sweep": 0}

    def _sweep(*, log: object) -> None:
        calls["sweep"] += 1

    monkeypatch.setattr(_hk, "_reap_orphans_once", lambda **_kw: None)
    monkeypatch.setattr(_hk, "_guard_daemon_log_size", lambda **_kw: None)

    async def _noop_follower_reap(*, log: object) -> None:
        return None

    monkeypatch.setattr(_hk, "_reap_dead_follower_sessions_once", _noop_follower_reap)
    monkeypatch.setattr(_hk, "_sweep_bridge_state_tmp_once", _sweep)
    log = MagicMock()

    async def _run() -> None:
        task = asyncio.create_task(_hk.daemon_housekeeping(interval_seconds=0.001, log=log))
        for _ in range(200):
            await asyncio.sleep(0.001)
            if calls["sweep"] >= 1:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert calls["sweep"] >= 1


class _FakeGatedSession:
    """Stand-in for BrowserSession -- just enough surface for job 6."""

    def __init__(self, instance_id: str, kind: str, outcome: bool | Exception) -> None:
        self.instance_id = instance_id
        self.kind = kind
        self._outcome = outcome

    async def enforce_operation_active_timeout(self, ceiling_seconds: float) -> bool:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def test_enforce_active_timeout_once_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping as _hk
    from octowright.server import pool as _pool

    monkeypatch.delenv("OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS", raising=False)
    iter_sessions = MagicMock(return_value=())
    monkeypatch.setattr(_pool, "iter_sessions", iter_sessions)
    log = MagicMock()

    asyncio.run(_hk._enforce_operation_active_timeout_once(log=log))

    # The pool must not even be consulted when the ceiling is off.
    iter_sessions.assert_not_called()
    log.warning.assert_not_called()


def test_enforce_active_timeout_once_breaches_only_the_wedged_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping as _hk
    from octowright.server import pool as _pool

    monkeypatch.setenv("OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS", "60")
    wedged = _FakeGatedSession("wedged-1", "chromium", True)
    healthy = _FakeGatedSession("healthy-1", "firefox", False)
    monkeypatch.setattr(_pool, "iter_sessions", lambda: (wedged, healthy))
    log = MagicMock()

    asyncio.run(_hk._enforce_operation_active_timeout_once(log=log))

    log.warning.assert_called_once_with(
        "octowright.housekeeping.active_timeout_breaches",
        count=1,
        session_ids=["wedged-1"],
    )


def test_enforce_active_timeout_once_isolates_a_per_session_check_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octowright import housekeeping as _hk
    from octowright.server import pool as _pool

    monkeypatch.setenv("OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS", "60")
    broken = _FakeGatedSession("broken-1", "chromium", RuntimeError("boom"))
    healthy = _FakeGatedSession("healthy-1", "firefox", True)
    monkeypatch.setattr(_pool, "iter_sessions", lambda: (broken, healthy))
    log = MagicMock()

    # One session's check raising must not stop the other from being checked.
    asyncio.run(_hk._enforce_operation_active_timeout_once(log=log))

    logged = {c.args[0] for c in log.warning.call_args_list}
    assert "octowright.housekeeping.active_timeout_check_failed" in logged
    assert "octowright.housekeeping.active_timeout_breaches" in logged
    breach_call = next(
        c for c in log.warning.call_args_list if c.args[0] == "octowright.housekeeping.active_timeout_breaches"
    )
    assert breach_call.kwargs["session_ids"] == ["healthy-1"]


def test_daemon_housekeeping_loop_runs_active_timeout_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping as _hk

    calls = {"active_timeout": 0}

    async def _active_timeout(*, log: object) -> None:
        calls["active_timeout"] += 1

    monkeypatch.setattr(_hk, "_reap_orphans_once", lambda **_kw: None)
    monkeypatch.setattr(_hk, "_guard_daemon_log_size", lambda **_kw: None)

    async def _noop_follower_reap(*, log: object) -> None:
        return None

    monkeypatch.setattr(_hk, "_reap_dead_follower_sessions_once", _noop_follower_reap)
    monkeypatch.setattr(_hk, "_sweep_bridge_state_tmp_once", lambda **_kw: None)
    monkeypatch.setattr(_hk, "_enforce_operation_active_timeout_once", _active_timeout)
    log = MagicMock()

    async def _run() -> None:
        task = asyncio.create_task(_hk.daemon_housekeeping(interval_seconds=0.001, log=log))
        for _ in range(200):
            await asyncio.sleep(0.001)
            if calls["active_timeout"] >= 1:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert calls["active_timeout"] >= 1


def test_daemon_housekeeping_loop_survives_active_timeout_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import housekeeping as _hk

    monkeypatch.setattr(_hk, "_reap_orphans_once", lambda **_kw: None)
    monkeypatch.setattr(_hk, "_guard_daemon_log_size", lambda **_kw: None)

    async def _noop_follower_reap(*, log: object) -> None:
        return None

    monkeypatch.setattr(_hk, "_reap_dead_follower_sessions_once", _noop_follower_reap)
    monkeypatch.setattr(_hk, "_sweep_bridge_state_tmp_once", lambda **_kw: None)

    async def _boom(*, log: object) -> None:
        raise RuntimeError("active timeout boom")

    monkeypatch.setattr(_hk, "_enforce_operation_active_timeout_once", _boom)
    log = MagicMock()

    async def _run() -> None:
        task = asyncio.create_task(_hk.daemon_housekeeping(interval_seconds=0.001, log=log))
        for _ in range(200):
            await asyncio.sleep(0.001)
            if log.warning.call_args_list:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    logged = {c.args[0] for c in log.warning.call_args_list}
    assert "octowright.housekeeping.active_timeout_failed" in logged
