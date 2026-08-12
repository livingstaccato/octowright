# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Daemonization integration test.

Spawns a real ``octowright serve`` subprocess with an isolated lockfile +
HTTP port, waits for it to fork its detached daemon leader, then SIGKILLs
the parent and verifies the daemon is still serving HTTP. This is the
architectural fix for "Claude Code closes → all browsers die" — the daemon
must outlive its launcher even on SIGKILL.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from octowright import daemonize as _daemon


def _resolve_octowright_entry() -> str:
    """Resolve the ``octowright`` console script for the current interpreter.

    uv (and pip) install entry-point scripts next to the interpreter, so the
    venv's ``bin/octowright`` is the canonical companion to ``sys.executable``.
    Falls back to ``shutil.which`` so a globally-installed shim still works
    (e.g. when a contributor uses pipx). Hardcoding an absolute path was the
    previous approach — that only worked on one developer's machine.
    """
    venv_bin = Path(sys.executable).parent / "octowright"
    if venv_bin.exists():
        return str(venv_bin)
    on_path = shutil.which("octowright")
    if on_path:
        return on_path
    return str(venv_bin)  # the skipif below will mark the test as skipped


OCTOWRIGHT = _resolve_octowright_entry()


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _read_lock(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="SIGKILL-based parent-survives test is POSIX-only; Windows uses TerminateProcess",
)
@pytest.mark.skipif(
    not Path(OCTOWRIGHT).exists(),
    reason="octowright entry point not installed in expected venv",
)
def test_daemon_survives_parent_sigkill(tmp_path: Path) -> None:
    """Spawn parent → wait for daemon → SIGKILL parent → daemon still serves HTTP."""
    from tests.conftest import _free_port

    lock_path = tmp_path / "octowright.lock"
    test_port = _free_port()  # OS-assigned free port, avoids collision with running daemons

    env = os.environ.copy()
    env["OCTOWRIGHT_LOCK_PATH"] = str(lock_path)
    env["OCTOWRIGHT_HTTP_PORT"] = str(test_port)
    # Long idle grace so the daemon doesn't quit on its own during the test.
    env["OCTOWRIGHT_IDLE_GRACE"] = "300"

    stderr_log = (tmp_path / "parent-stderr.log").open("wb")
    parent = subprocess.Popen(
        [OCTOWRIGHT, "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr_log,
        env=env,
    )

    daemon_pid: int | None = None
    try:
        # Wait up to 12s for the daemon to write the lockfile and serve health.
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            time.sleep(0.3)
            info = _read_lock(lock_path)
            if info is None:
                continue
            pid = info.get("pid")
            if not isinstance(pid, int) or pid == parent.pid:
                # Either no lock yet, or parent itself wrote it (shouldn't happen
                # in the daemon path). Keep waiting.
                continue
            try:
                resp = httpx.get(
                    f"http://{info['http_host']}:{info['http_port']}/api/health",
                    timeout=2.0,
                )
            except (httpx.HTTPError, OSError):
                continue
            if resp.status_code == 200:
                daemon_pid = pid
                break
        assert daemon_pid is not None, "daemon never came up within timeout"
        assert daemon_pid != parent.pid, f"parent {parent.pid} is the leader — daemon was not spawned"

        # The crucial test: SIGKILL the parent (uncatchable, mimics worst-case
        # MCP-launcher behaviour). The daemon is in a different session so the
        # signal can't propagate.
        _kill_pid(parent.pid)
        time.sleep(1.5)

        # Daemon must still be alive AND still serving /api/health.
        try:
            os.kill(daemon_pid, 0)
        except ProcessLookupError:
            pytest.fail(f"daemon PID {daemon_pid} died with parent — daemonization is broken")

        info = _read_lock(lock_path)
        assert info is not None and info.get("pid") == daemon_pid, "lockfile mutated unexpectedly"
        resp = httpx.get(f"http://{info['http_host']}:{info['http_port']}/api/health", timeout=2.0)
        assert resp.status_code == 200, f"daemon HTTP unhealthy after parent kill: {resp.status_code}"
    finally:
        # Always clean up — kill both parent and daemon if either is still alive.
        if parent.poll() is None:
            _kill_pid(parent.pid)
            parent.wait(timeout=3)
        if daemon_pid is not None:
            _kill_pid(daemon_pid)
            # Reap the orphan with waitpid? It's not our child; OS handles it.


# ─── _resolve_daemon_entrypoint ──────────────────────────────────────────────


class TestResolveDaemonEntrypoint:
    """Pin the entrypoint resolver. ``sys.argv[0]`` is unreliable for the
    ``python -m octowright`` launch path; the resolver must prefer the
    installed console script, then fall back to ``python -m octowright``.
    """

    def test_prefers_shutil_which_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If `octowright` is on PATH, that absolute path is the entrypoint."""
        monkeypatch.setattr(_daemon.shutil, "which", lambda _name: "/opt/venv/bin/octowright")
        assert _daemon._resolve_daemon_entrypoint() == ["/opt/venv/bin/octowright"]

    def test_falls_back_to_python_m_octowright(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When `octowright` is NOT on PATH, fall back to `python -m octowright`."""
        monkeypatch.setattr(_daemon.shutil, "which", lambda _name: None)
        monkeypatch.setattr(_daemon.sys, "executable", "/usr/bin/python3.13")
        assert _daemon._resolve_daemon_entrypoint() == ["/usr/bin/python3.13", "-m", "octowright"]

    def test_last_resort_uses_sys_argv0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If both PATH lookup and sys.executable are unavailable, fall back to argv[0]."""
        monkeypatch.setattr(_daemon.shutil, "which", lambda _name: None)
        monkeypatch.setattr(_daemon.sys, "executable", "")
        monkeypatch.setattr(_daemon.sys, "argv", ["/some/weird/path"])
        assert _daemon._resolve_daemon_entrypoint() == ["/some/weird/path"]

    def test_spawn_daemon_uses_resolver_for_argv_prefix(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """spawn_daemon should prepend the resolver's argv prefix, not raw sys.argv[0]."""
        monkeypatch.setattr(_daemon, "_DAEMON_LOG", tmp_path / "d.log")
        monkeypatch.setattr(_daemon, "_resolve_daemon_entrypoint", lambda: ["/usr/bin/python3", "-m", "octowright"])

        captured: dict[str, Any] = {}

        def fake_popen(args: list[str], **kwargs: Any) -> Any:
            captured["args"] = args
            stderr = kwargs.get("stderr")
            if hasattr(stderr, "close"):
                stderr.close()

            class _Fake:
                pid = 4321

            return _Fake()

        monkeypatch.setattr(_daemon.subprocess, "Popen", fake_popen)
        _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
        assert captured["args"][:5] == ["/usr/bin/python3", "-m", "octowright", "serve", "--daemon-mode"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission-bit assertion")
def test_open_daemon_log_repairs_legacy_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-existing permissive daemon log must not keep exposing secrets."""
    log_path = tmp_path / "octowright-daemon.log"
    log_path.write_bytes(b"legacy\n")
    log_path.chmod(0o644)
    monkeypatch.setattr(_daemon, "_DAEMON_LOG", log_path)

    handle = _daemon._open_daemon_log()
    handle.close()

    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


def test_open_daemon_log_chmod_failure_does_not_block_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported chmod semantics must not take down daemon startup."""
    log_path = tmp_path / "octowright-daemon.log"
    monkeypatch.setattr(_daemon, "_DAEMON_LOG", log_path)
    monkeypatch.setattr(_daemon.os, "fchmod", lambda *_args: (_ for _ in ()).throw(OSError("unsupported")))
    monkeypatch.setattr(_daemon.os, "chmod", lambda *_args: (_ for _ in ()).throw(OSError("unsupported")))

    handle = _daemon._open_daemon_log()
    handle.write(b"still starts\n")
    handle.close()

    assert log_path.read_bytes() == b"still starts\n"
