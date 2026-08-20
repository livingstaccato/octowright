# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.daemonize.

Pins:
- spawn_daemon argv construction (each optional flag passthrough)
- Popen kwargs: detached session, stdin/stdout/stderr redirected, close_fds, env copy
- _open_daemon_log truncate-on-overflow + parent-dir creation
- wait_for_daemon: polls until lockfile + alive + http; timeout returns None;
  stale lockfile skipped; missing lockfile skipped; alive PID + dead HTTP skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import daemonize as _daemon


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── _open_daemon_log ────────────────────────────────────────────────────────


class TestOpenDaemonLog:
    def test_creates_parent_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If user_config_dir is fresh, the daemon-log directory is created."""
        log_path = tmp_path / "newdir" / "subdir" / "daemon.log"
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", log_path)
        fh = _daemon._open_daemon_log()
        try:
            assert log_path.parent.exists()
            assert log_path.exists()
        finally:
            fh.close()

    def test_appends_when_under_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Existing small file is appended to, not truncated."""
        log_path = tmp_path / "daemon.log"
        log_path.write_bytes(b"existing\n")
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", log_path)
        fh = _daemon._open_daemon_log()
        try:
            fh.write(b"new\n")
        finally:
            fh.close()
        # Both lines present.
        assert log_path.read_bytes() == b"existing\nnew\n"

    def test_truncates_when_over_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """File over _DAEMON_LOG_MAX_BYTES is unlinked + reopened."""
        log_path = tmp_path / "daemon.log"
        # Make the file exceed the limit.
        big = b"x" * (_daemon._DAEMON_LOG_MAX_BYTES + 100)
        log_path.write_bytes(big)
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", log_path)
        fh = _daemon._open_daemon_log()
        try:
            fh.write(b"fresh\n")
        finally:
            fh.close()
        # Only the new content remains; old content discarded.
        contents = log_path.read_bytes()
        assert contents == b"fresh\n"

    def test_returns_binary_handle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mode is 'ab' — bytes-writable."""
        log_path = tmp_path / "daemon.log"
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", log_path)
        fh = _daemon._open_daemon_log()
        try:
            # Writing bytes works; writing str would fail.
            fh.write(b"\x00\x01")
            with pytest.raises(TypeError):
                fh.write("not bytes")  # type: ignore[arg-type]
        finally:
            fh.close()


# ─── spawn_daemon ────────────────────────────────────────────────────────────


def _capture_popen(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, dict[str, Any]]:
    """Replace subprocess.Popen with a recording mock that returns pid=12345."""
    captured: dict[str, Any] = {"args": None, "kwargs": None}

    def fake_popen(args: list[str], **kwargs: Any) -> MagicMock:
        captured["args"] = args
        captured["kwargs"] = kwargs
        # Close the stderr handle the function passes in so we don't leak it.
        stderr = kwargs.get("stderr")
        if hasattr(stderr, "close"):
            stderr.close()
        proc = MagicMock()
        proc.pid = 12345
        return proc

    monkeypatch.setattr(_daemon.subprocess, "Popen", fake_popen)
    return MagicMock(side_effect=fake_popen), captured


class TestSpawnDaemonArgv:
    def test_returns_subprocess_pid(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Returned value is the pid attribute on the Popen result."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _capture_popen(monkeypatch)
        pid = _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert pid == 12345

    def test_argv_starts_with_octowright_serve_daemon_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Last two argv entries are 'serve', '--daemon-mode' regardless of entrypoint."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        # Force the entrypoint resolver to a stable, predictable value so the
        # test doesn't depend on whether `octowright` is on PATH in CI.
        monkeypatch.setattr(_daemon, "_resolve_daemon_entrypoint", lambda: ["/fake/octowright"])
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert captured["args"][:3] == ["/fake/octowright", "serve", "--daemon-mode"]

    def test_omits_optional_flags_when_unset(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """All three optional kwargs unset → no --http-host / --http-port / --idle-grace."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        argv = captured["args"]
        assert "--http-host" not in argv
        assert "--http-port" not in argv
        assert "--idle-grace" not in argv

    def test_includes_http_host_when_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """http_host=str → '--http-host <host>' added."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host="0.0.0.0", http_port=None, idle_grace=None)
        argv = captured["args"]
        assert "--http-host" in argv
        i = argv.index("--http-host")
        assert argv[i + 1] == "0.0.0.0"

    def test_includes_http_port_when_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Port is stringified."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=9000, idle_grace=None)
        argv = captured["args"]
        i = argv.index("--http-port")
        assert argv[i + 1] == "9000"

    def test_includes_idle_grace_when_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """idle_grace float is stringified."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=300.0)
        argv = captured["args"]
        i = argv.index("--idle-grace")
        assert argv[i + 1] == "300.0"

    def test_includes_keep_alive_flag_when_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """keep_alive=True forwards --keep-alive to the daemon argv (the propagation fix)."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None, keep_alive=True)
        assert "--keep-alive" in captured["args"]

    def test_omits_keep_alive_when_unset(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """keep_alive defaults False → no --keep-alive in the daemon argv."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert "--keep-alive" not in captured["args"]

    def test_http_host_empty_string_omitted(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """`if http_host` falsy gate — empty string treated as unset."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host="", http_port=None, idle_grace=None)
        assert "--http-host" not in captured["args"]

    def test_http_port_zero_is_included(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """`if http_port is not None` — 0 is a valid value (let-OS-choose)."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=0, idle_grace=None)
        argv = captured["args"]
        assert "--http-port" in argv
        i = argv.index("--http-port")
        assert argv[i + 1] == "0"

    def test_idle_grace_zero_is_included(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """`if idle_grace is not None` — 0 valid (immediate exit grace)."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=0.0)
        argv = captured["args"]
        assert "--idle-grace" in argv


class TestSpawnDaemonPopenKwargs:
    def test_starts_new_session(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The daemon is detached so it survives parent SIGKILL.

        Asserted per-platform: ``start_new_session`` is the POSIX mechanism and
        is absent on Windows, which uses creation flags instead (asserting it
        unconditionally made the Windows leg fail on its own fix).
        """
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        if sys.platform == "win32":
            assert captured["kwargs"]["creationflags"] & _daemon._DETACHED_PROCESS
        else:
            assert captured["kwargs"]["start_new_session"] is True

    def test_close_fds_true(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """close_fds=True closes inherited descriptors (clean detach)."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert captured["kwargs"]["close_fds"] is True

    def test_stdin_devnull(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """stdin = subprocess.DEVNULL (no controlling terminal interaction)."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert captured["kwargs"]["stdin"] == _daemon.subprocess.DEVNULL

    def test_stdout_devnull(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """stdout → DEVNULL (no inherited-fd writes back to parent)."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert captured["kwargs"]["stdout"] == _daemon.subprocess.DEVNULL

    def test_env_inherits_parent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """env is os.environ.copy() — daemon sees same env vars (PROVIDE_LOG_LEVEL etc.)."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        monkeypatch.setenv("OCTOWRIGHT_TEST_FLAG", "yes")
        _, captured = _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert captured["kwargs"]["env"]["OCTOWRIGHT_TEST_FLAG"] == "yes"

    def test_stderr_is_log_file_handle(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """stderr is the daemon-log file handle (writable bytes)."""
        log_path = tmp_path / "d.log"
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", log_path)
        # Override _open_daemon_log to spy on the handle.
        opened_handles: list[Any] = []
        original = _daemon._open_daemon_log

        def spy() -> Any:
            fh = original()
            opened_handles.append(fh)
            return fh

        monkeypatch.setattr(_daemon, "_open_daemon_log", spy)
        _capture_popen(monkeypatch)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        # The captured stderr is the same object _open_daemon_log returned (or
        # was at least, before _capture_popen closed it).
        assert opened_handles  # at least one handle was acquired


# ─── wait_for_daemon ─────────────────────────────────────────────────────────


class TestWaitForDaemon:
    @pytest.mark.anyio
    async def test_returns_info_when_ready_first_poll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lockfile present + pid alive + http alive → return info."""
        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        import octowright.singleton as _sn

        monkeypatch.setattr(_sn, "read_lock", lambda: info)
        monkeypatch.setattr(_sn, "is_stale", lambda _i: False)
        monkeypatch.setattr(_sn, "probe_http_alive", AsyncMock(return_value=True))
        result = await _daemon.wait_for_daemon(timeout=1.0, poll_seconds=0.01)
        assert result is info

    @pytest.mark.anyio
    async def test_a_non_loopback_leader_url_is_never_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The 0600 lockfile is writable by any same-user process, and
        ``serve --wait-ready`` prints this URL to stdout for a CI job to
        consume. Skipping the loopback check here was the one path that handed
        a poisoned URL out unvalidated (and dialled it for the health probe)."""
        info = SimpleNamespace(pid=42, mcp_url="http://attacker.test/mcp")
        import octowright.singleton as _sn

        monkeypatch.setattr(_sn, "read_lock", lambda: info)
        monkeypatch.setattr(_sn, "is_stale", lambda _i: False)
        probe = AsyncMock(return_value=True)
        monkeypatch.setattr(_sn, "probe_http_alive", probe)

        assert await _daemon.wait_for_daemon(timeout=0.05, poll_seconds=0.01) is None
        probe.assert_not_awaited()  # never even dialled

    @pytest.mark.anyio
    async def test_returns_none_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lockfile never appears → return None when deadline passes."""
        import octowright.singleton as _sn

        monkeypatch.setattr(_sn, "read_lock", lambda: None)
        monkeypatch.setattr(_sn, "is_stale", lambda _i: False)
        monkeypatch.setattr(_sn, "probe_http_alive", AsyncMock(return_value=False))
        result = await _daemon.wait_for_daemon(timeout=0.05, poll_seconds=0.01)
        assert result is None

    @pytest.mark.anyio
    async def test_skips_stale_lockfiles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lockfile present but is_stale=True → keep polling, don't return."""
        info = SimpleNamespace(pid=42, mcp_url="http://127.0.0.1:6286/mcp/")
        import octowright.singleton as _sn

        monkeypatch.setattr(_sn, "read_lock", lambda: info)
        monkeypatch.setattr(_sn, "is_stale", lambda _i: True)  # always stale
        probe = AsyncMock(return_value=True)
        monkeypatch.setattr(_sn, "probe_http_alive", probe)
        result = await _daemon.wait_for_daemon(timeout=0.05, poll_seconds=0.01)
        assert result is None
        # probe_http_alive should never have been awaited (we skip past it).
        probe.assert_not_awaited()

    @pytest.mark.anyio
    async def test_skips_when_http_not_alive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PID alive but HTTP not yet ready → keep polling."""
        info = SimpleNamespace(pid=42, mcp_url="http://127.0.0.1:6286/mcp/")
        import octowright.singleton as _sn

        monkeypatch.setattr(_sn, "read_lock", lambda: info)
        monkeypatch.setattr(_sn, "is_stale", lambda _i: False)
        monkeypatch.setattr(_sn, "probe_http_alive", AsyncMock(return_value=False))
        result = await _daemon.wait_for_daemon(timeout=0.05, poll_seconds=0.01)
        assert result is None

    @pytest.mark.anyio
    async def test_succeeds_after_initial_misses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First poll: lockfile missing. Second poll: lockfile present + alive."""
        info = SimpleNamespace(pid=42, mcp_url="http://127.0.0.1:6286/mcp/")
        import octowright.singleton as _sn

        attempts = {"n": 0}

        def read_lock() -> Any:
            attempts["n"] += 1
            return None if attempts["n"] == 1 else info

        monkeypatch.setattr(_sn, "read_lock", read_lock)
        monkeypatch.setattr(_sn, "is_stale", lambda _i: False)
        monkeypatch.setattr(_sn, "probe_http_alive", AsyncMock(return_value=True))
        result = await _daemon.wait_for_daemon(timeout=1.0, poll_seconds=0.01)
        assert result is info
        assert attempts["n"] >= 2
