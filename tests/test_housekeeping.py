# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader housekeeping: orphan reap pass + in-place daemon-log truncation."""

from __future__ import annotations

import asyncio
import os
import stat as _stat
from unittest.mock import MagicMock

import pytest

from octowright import housekeeping


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
    log = MagicMock()
    housekeeping.reap_orphan_browsers_at_boot(log=log)  # must not raise
    log.warning.assert_called_once()
    assert log.warning.call_args.args[0] == "octowright.boot.orphan_reap_failed"


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
