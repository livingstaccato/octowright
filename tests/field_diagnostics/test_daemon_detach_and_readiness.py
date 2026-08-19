# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The daemon must actually detach, and say why when it doesn't come up.

Field report from a Windows CI leg: ``start_new_session=True`` is POSIX-only,
so the "daemon" stayed inside the step's process tree and died with it; the
10s readiness budget was unreachable from the CLI; and the workflow's own log
was empty by design because daemon stderr goes to a separate file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from octowright import daemonize


class TestDetachFlags:
    def test_posix_uses_a_new_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(daemonize.sys, "platform", "linux")
        assert daemonize._detach_kwargs() == {"start_new_session": True}

    def test_windows_uses_creation_flags_not_the_posix_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The regression: CPython accepts start_new_session on Windows and
        silently ignores it, leaving the daemon in the parent's job object."""
        monkeypatch.setattr(daemonize.sys, "platform", "win32")

        kwargs = daemonize._detach_kwargs()

        assert "start_new_session" not in kwargs
        assert kwargs == {"creationflags": daemonize._DETACHED_PROCESS | daemonize._CREATE_NEW_PROCESS_GROUP}

    def test_spawn_passes_the_platform_flags_through(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", tmp_path / "daemon.log")
        monkeypatch.setattr(daemonize.sys, "platform", "win32")
        seen: dict[str, Any] = {}

        class _Proc:
            pid = 4242

        def _popen(_args: list[str], **kwargs: Any) -> _Proc:
            seen.update(kwargs)
            return _Proc()

        monkeypatch.setattr(subprocess, "Popen", _popen)

        assert daemonize.spawn_daemon(http_host=None, http_port=None, idle_grace=None) == 4242
        assert seen["creationflags"] == daemonize._DETACHED_PROCESS | daemonize._CREATE_NEW_PROCESS_GROUP
        assert "start_new_session" not in seen


class TestReadyTimeout:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(daemonize.DAEMON_READY_TIMEOUT_ENV, raising=False)
        assert daemonize.daemon_ready_timeout() == daemonize.DAEMON_READY_TIMEOUT_SECONDS

    def test_env_override_reaches_a_cold_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(daemonize.DAEMON_READY_TIMEOUT_ENV, " 45 ")
        assert daemonize.daemon_ready_timeout() == 45.0

    @pytest.mark.parametrize("raw", ["garbage", "", "0", "-3"])
    def test_unusable_values_fall_back_rather_than_hanging_or_never_waiting(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv(daemonize.DAEMON_READY_TIMEOUT_ENV, raw)
        assert daemonize.daemon_ready_timeout() == daemonize.DAEMON_READY_TIMEOUT_SECONDS

    @pytest.mark.anyio
    async def test_wait_for_daemon_reads_the_env_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The regression: the 10s default was hardcoded in the signature and
        every caller invoked it with no arguments, so it was unreachable."""
        monkeypatch.setenv(daemonize.DAEMON_READY_TIMEOUT_ENV, "0.05")
        monkeypatch.setattr(daemonize._sn, "read_lock", lambda: None)

        assert await daemonize.wait_for_daemon(poll_seconds=0.01) is None

    @pytest.mark.anyio
    async def test_explicit_timeout_still_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(daemonize.DAEMON_READY_TIMEOUT_ENV, "600")
        monkeypatch.setattr(daemonize._sn, "read_lock", lambda: None)

        assert await daemonize.wait_for_daemon(timeout=0.05, poll_seconds=0.01) is None


class TestDaemonLogTail:
    def test_reports_the_last_lines(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        log = tmp_path / "daemon.log"
        log.write_text("\n".join(f"line {index}" for index in range(50)), encoding="utf-8")
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", log)

        tail = daemonize.daemon_log_tail(3)

        assert tail == "line 47\nline 48\nline 49"

    def test_missing_log_is_itself_the_diagnostic(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", tmp_path / "absent.log")

        tail = daemonize.daemon_log_tail()

        assert "may never have started" in tail
        assert "absent.log" in tail

    def test_empty_log_says_so_rather_than_returning_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        log = tmp_path / "daemon.log"
        log.write_text("\n  \n\n", encoding="utf-8")
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", log)

        assert "is empty" in daemonize.daemon_log_tail()

    def test_unreadable_log_never_raises_into_the_failure_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """This runs while reporting another failure; it must not add one."""
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", tmp_path)  # a directory

        assert "unreadable" in daemonize.daemon_log_tail()

    def test_path_accessor_points_at_the_real_log(self) -> None:
        assert daemonize.daemon_log_path() == daemonize._DAEMON_LOG
