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
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


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
    not Path(OCTOWRIGHT).exists(),
    reason="octowright entry point not installed in expected venv",
)
def test_daemon_survives_parent_sigkill(tmp_path: Path) -> None:
    """Spawn parent → wait for daemon → SIGKILL parent → daemon still serves HTTP."""
    lock_path = tmp_path / "octowright.lock"
    test_port = 18950  # well above the user's range

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
