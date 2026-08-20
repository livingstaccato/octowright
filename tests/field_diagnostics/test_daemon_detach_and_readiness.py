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
from types import SimpleNamespace
from typing import Any

import pytest

from octowright import daemonize


def _windows_error(winerror: int, message: str) -> OSError:
    """An OSError shaped like the one CreateProcess raises on Windows.

    ``winerror`` is the field the retry ladder keys off; it does not exist on
    POSIX, where this suite runs, so the fake has to carry it explicitly.
    """
    exc = OSError(message)
    exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


def _access_denied() -> OSError:
    return _windows_error(daemonize._ERROR_ACCESS_DENIED, "Access is denied")


class TestDetachFlags:
    def test_posix_uses_a_new_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(daemonize.sys, "platform", "linux")
        assert daemonize._detach_kwargs() == {"start_new_session": True}

    def test_windows_uses_creation_flags_not_the_posix_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The regression: CPython accepts start_new_session on Windows and
        silently ignores it, leaving the daemon attached to its parent."""
        monkeypatch.setattr(daemonize.sys, "platform", "win32")

        kwargs = daemonize._detach_kwargs()

        assert "start_new_session" not in kwargs
        assert kwargs == {
            "creationflags": (
                daemonize._DETACHED_PROCESS | daemonize._CREATE_NEW_PROCESS_GROUP | daemonize._CREATE_BREAKAWAY_FROM_JOB
            )
        }

    def test_windows_console_detachment_alone_is_not_enough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DETACHED_PROCESS only detaches the console. A CI runner kills its
        job object, which takes the daemon with it unless it breaks away."""
        monkeypatch.setattr(daemonize.sys, "platform", "win32")

        assert daemonize._detach_kwargs()["creationflags"] & daemonize._CREATE_BREAKAWAY_FROM_JOB
        assert not daemonize._detach_kwargs(breakaway=False)["creationflags"] & daemonize._CREATE_BREAKAWAY_FROM_JOB

    def test_posix_gets_exactly_one_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The retry exists for a Windows job refusal. A blanket OSError retry
        would run a second identical Popen for a genuine POSIX failure."""
        monkeypatch.setattr(daemonize.sys, "platform", "linux")
        assert len(daemonize._detach_candidates()) == 1

        monkeypatch.setattr(daemonize.sys, "platform", "win32")
        assert len(daemonize._detach_candidates()) == 2

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
        assert seen["creationflags"] & daemonize._CREATE_BREAKAWAY_FROM_JOB
        assert "start_new_session" not in seen

    def test_a_job_that_forbids_breakaway_falls_back_instead_of_failing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """CreateProcess refuses CREATE_BREAKAWAY_FROM_JOB outright when the
        job lacks JOB_OBJECT_LIMIT_BREAKAWAY_OK. Retry without it rather than
        failing the spawn -- console detachment alone is still an improvement."""
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", tmp_path / "daemon.log")
        monkeypatch.setattr(daemonize.sys, "platform", "win32")
        attempts: list[int] = []

        class _Proc:
            pid = 7

        def _popen(_args: list[str], **kwargs: Any) -> _Proc:
            attempts.append(kwargs["creationflags"])
            if kwargs["creationflags"] & daemonize._CREATE_BREAKAWAY_FROM_JOB:
                raise _access_denied()
            return _Proc()

        monkeypatch.setattr(subprocess, "Popen", _popen)

        assert daemonize.spawn_daemon(http_host=None, http_port=None, idle_grace=None) == 7
        assert len(attempts) == 2
        assert not attempts[1] & daemonize._CREATE_BREAKAWAY_FROM_JOB

    def test_a_windows_failure_that_is_not_the_breakaway_one_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The ladder retries ONE failure. Retrying any OSError ran a second
        identical Popen for a genuine problem and then reported the SECOND
        exception, discarding the one that named the cause."""
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", tmp_path / "daemon.log")
        monkeypatch.setattr(daemonize.sys, "platform", "win32")
        attempts: list[int] = []

        def _popen(_args: list[str], **kwargs: Any) -> None:
            attempts.append(kwargs["creationflags"])
            raise _windows_error(2, "The system cannot find the file specified")

        monkeypatch.setattr(subprocess, "Popen", _popen)

        with pytest.raises(OSError, match="cannot find the file"):
            daemonize.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert len(attempts) == 1

    def test_a_real_spawn_failure_still_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Only the breakaway flag is retryable; a missing executable is not."""
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", tmp_path / "daemon.log")

        def _popen(_args: list[str], **_kwargs: Any) -> None:
            raise OSError(2, "No such file or directory")

        monkeypatch.setattr(subprocess, "Popen", _popen)

        with pytest.raises(OSError, match="No such file"):
            daemonize.spawn_daemon(http_host=None, http_port=None, idle_grace=None)

    def test_the_parent_does_not_leak_the_log_descriptor(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Popen dups the handle into the child; the parent's copy must close
        or a long-lived follower leaks one fd per respawn."""
        monkeypatch.setattr(daemonize, "_DAEMON_LOG", tmp_path / "daemon.log")
        handles: list[Any] = []
        real_open = daemonize._open_daemon_log

        def _tracking_open() -> Any:
            handle = real_open()
            handles.append(handle)
            return handle

        monkeypatch.setattr(daemonize, "_open_daemon_log", _tracking_open)

        class _Proc:
            pid = 11

        monkeypatch.setattr(subprocess, "Popen", lambda _a, **_k: _Proc())

        daemonize.spawn_daemon(http_host=None, http_port=None, idle_grace=None)

        assert handles and all(handle.closed for handle in handles)


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


class TestContendedElectionIsNotAFailure:
    """Another instance holding the election lock is already electing the leader
    we want. Three call sites used to answer this one condition three different
    ways -- and --wait-ready's answer was to fail, which would flake in exactly
    the concurrent-startup case CI creates."""

    @pytest.mark.anyio
    async def test_contention_waits_for_the_winners_leader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from octowright import daemonize as _daemonize
        from octowright import singleton as _sn
        from octowright.cli import _leader_election as _election

        leader = SimpleNamespace(mcp_url="http://127.0.0.1:6286/mcp")

        class _Contended:
            def __init__(self, *_a: Any, **_k: Any) -> None:
                pass

            async def __aenter__(self) -> None:
                raise TimeoutError

            async def __aexit__(self, *_exc: Any) -> None:
                return None

        async def _no_leader(_sn_mod: Any) -> Any:
            return None

        async def _wait(*_a: Any, **_k: Any) -> Any:
            return leader

        def _spawn(**_k: Any) -> int:
            raise AssertionError("spawned a competitor while another instance held the lock")

        monkeypatch.setattr(_election, "_probe_alive_leader", _no_leader)
        monkeypatch.setattr(_sn, "async_election_lock", _Contended)
        monkeypatch.setattr(_daemonize, "wait_for_daemon", _wait)
        monkeypatch.setattr(_daemonize, "spawn_daemon", _spawn)

        result = await _election.elect_leader(http_host=None, http_port=None, idle_grace=None, keep_alive=False)

        assert result is leader

    @pytest.mark.anyio
    async def test_contention_that_never_produces_a_leader_is_reported_as_contention(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """We spawned nothing, so this is NOT a spawn failure. Conflating them
        made the caller quote a daemon log describing a process it never
        started and record inline_reason="daemon_spawn_failed" for a spawn that
        did not happen."""
        from octowright import daemonize as _daemonize
        from octowright import singleton as _sn
        from octowright.cli import _leader_election as _election

        class _Contended:
            def __init__(self, *_a: Any, **_k: Any) -> None:
                pass

            async def __aenter__(self) -> None:
                raise TimeoutError

            async def __aexit__(self, *_exc: Any) -> None:
                return None

        async def _no_leader(_sn_mod: Any) -> Any:
            return None

        async def _never_ready(*_a: Any, **_k: Any) -> Any:
            return None

        monkeypatch.setattr(_election, "_probe_alive_leader", _no_leader)
        monkeypatch.setattr(_sn, "async_election_lock", _Contended)
        monkeypatch.setattr(_daemonize, "wait_for_daemon", _never_ready)

        with pytest.raises(_election.ElectionContended):
            await _election.elect_leader(http_host=None, http_port=None, idle_grace=None, keep_alive=False)

    @pytest.mark.anyio
    async def test_a_timeout_from_inside_the_lock_is_not_mistaken_for_contention(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TimeoutError is an OSError subclass (and asyncio.TimeoutError's
        alias), so wrapping the whole body meant a timeout raised while
        probing/adopting/spawning printed "someone else is electing" and burned
        a second readiness budget waiting for a daemon nobody was starting."""
        from octowright import singleton as _sn
        from octowright.cli import _leader_election as _election

        class _Granted:
            def __init__(self, *_a: Any, **_k: Any) -> None:
                pass

            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_exc: Any) -> None:
                return None

        async def _no_leader_outside(_sn_mod: Any) -> Any:
            return None

        async def _boom(*_a: Any, **_k: Any) -> Any:
            raise TimeoutError("the probe itself timed out")

        monkeypatch.setattr(_election, "_probe_alive_leader", _no_leader_outside)
        monkeypatch.setattr(_sn, "async_election_lock", _Granted)
        monkeypatch.setattr(_election, "_elect_under_lock", _boom)

        with pytest.raises(TimeoutError):
            await _election.elect_leader(http_host=None, http_port=None, idle_grace=None, keep_alive=False)
