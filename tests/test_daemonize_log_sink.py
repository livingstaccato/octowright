# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The daemon log file is not always the daemon's log.

``daemonize`` redirects stderr only for daemons it spawned; a leader started
directly by a service manager (systemd ``octowright.service``) logs to the
journal, and the file then freezes while the daemon is healthy — observed on
this repo's own host, where the log froze for three weeks and every error
message quoting it was fiction. ``daemon_log_tail`` must say so instead of
presenting the stale file as current output.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from octowright import daemonize


def _lock(pid: int, started_at: float) -> SimpleNamespace:
    return SimpleNamespace(pid=pid, started_at=started_at)


def _write_log(path: Path, *, age_seconds: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("2026-10-09T09:11:55Z [INFO] octowright.http.stopped\n", encoding="utf-8")
    mtime = 1_000_000.0 + age_seconds
    os.utime(path, (mtime, mtime))


def test_note_when_leader_is_younger_than_the_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "octowright-daemon.log"
    _write_log(log_path)  # mtime 1_000_000
    monkeypatch.setattr(daemonize, "_DAEMON_LOG", log_path)
    monkeypatch.setattr(daemonize._sn, "read_lock", lambda: _lock(7489, 2_000_000.0))
    monkeypatch.setattr(daemonize._sn, "pid_is_alive", lambda _pid: True)
    tail = daemonize.daemon_log_tail()
    assert "journalctl --user -u octowright.service" in tail
    assert "octowright.http.stopped" in tail, "the real tail is still presented above the note"


def test_no_note_when_log_covers_the_running_leader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "octowright-daemon.log"
    _write_log(log_path)
    monkeypatch.setattr(daemonize, "_DAEMON_LOG", log_path)
    # Leader older than the log: an election spawn, whose stderr IS this file.
    monkeypatch.setattr(daemonize._sn, "read_lock", lambda: _lock(7489, 999_999.0))
    monkeypatch.setattr(daemonize._sn, "pid_is_alive", lambda _pid: True)
    assert "journalctl" not in daemonize.daemon_log_tail()


def test_no_note_without_a_running_leader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "octowright-daemon.log"
    _write_log(log_path)
    monkeypatch.setattr(daemonize, "_DAEMON_LOG", log_path)
    monkeypatch.setattr(daemonize._sn, "read_lock", lambda: None)
    assert "journalctl" not in daemonize.daemon_log_tail()


def test_missing_log_with_a_running_leader_points_at_the_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "logs" / "octowright-daemon.log"
    monkeypatch.setattr(daemonize, "_DAEMON_LOG", log_path)
    monkeypatch.setattr(daemonize._sn, "read_lock", lambda: _lock(7489, 2_000_000.0))
    monkeypatch.setattr(daemonize._sn, "pid_is_alive", lambda _pid: True)
    tail = daemonize.daemon_log_tail()
    assert "no daemon log" in tail
    assert "journalctl --user -u octowright.service" in tail


def test_an_empty_daemon_log_says_so_and_still_notes_the_sink(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "octowright-daemon.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("", encoding="utf-8")
    mtime = 1_000_000.0
    os.utime(log_path, (mtime, mtime))
    monkeypatch.setattr(daemonize, "_DAEMON_LOG", log_path)
    monkeypatch.setattr(daemonize._sn, "read_lock", lambda: _lock(7489, 2_000_000.0))
    monkeypatch.setattr(daemonize._sn, "pid_is_alive", lambda _pid: True)
    tail = daemonize.daemon_log_tail()
    assert "is empty" in tail
    assert "journalctl --user -u octowright.service" in tail
