# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Drive a real ``octowright serve`` subprocess over its stdio, on any platform.

Portable by construction: every pipe is drained by a reader thread into a
queue, so nothing here selects on a pipe or sets it non-blocking (neither works
on a Windows anonymous pipe). Each reader puts ``None`` on EOF, which is how a
test sees that every holder of a pipe's write end has gone -- including a
detached daemon that should never have inherited it.

Hermetic: :func:`isolated_env` points every state, config, cache, lock and
recordings path under the test's tmp dir, on both the XDG and the Windows
(``LOCALAPPDATA``/``APPDATA``) lookups, and :func:`free_port` never returns the
canonical 6286/6287.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import signal
import socket
import subprocess  # nosec B404
import sys
import threading
import time
from pathlib import Path
from typing import Any

# How long a cold interpreter may take to import the follower and open stdio.
# Windows runners routinely take several times longer than a Linux dev box.
STARTUP_TIMEOUT_SECONDS = 120.0 if sys.platform == "win32" else 60.0
# How fast ``initialize``/``ping`` must be answered once the follower is up.
# Generous: the claim is "answered locally, not after the election", and the
# elections these tests stage take far longer than either bound.
HANDSHAKE_BOUND_SECONDS = 5.0 if sys.platform == "win32" else 2.0

_SKIP_CANONICAL = {6286, 6287}


def free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in _SKIP_CANONICAL:
            return port


def isolated_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("OCTOWRIGHT_PLUGINS", None)
    env["XDG_STATE_HOME"] = str(root / "state")
    env["XDG_CONFIG_HOME"] = str(root / "config")
    env["XDG_CACHE_HOME"] = str(root / "cache")
    # ``config_paths`` reads these instead of XDG on Windows; left alone, the
    # daemon log and every unlisted default would land in the runner's profile.
    env["LOCALAPPDATA"] = str(root / "localappdata")
    env["APPDATA"] = str(root / "appdata")
    env.update(
        {
            "OCTOWRIGHT_HEADLESS": "1",
            "OCTOWRIGHT_HTTP_HOST": "127.0.0.1",
            "OCTOWRIGHT_HOUSEKEEPING_SECONDS": "off",
            "OCTOWRIGHT_LOCK_PATH": str(root / "state" / "octowright.lock"),
            "OCTOWRIGHT_BRIDGE_STATE": str(root / "state" / "bridge-state.json"),
            "OCTOWRIGHT_RECORDINGS": str(root / "state" / "sessions"),
            "OCTOWRIGHT_SESSION_MANIFEST": str(root / "state" / "session-manifest.json"),
            "OCTOWRIGHT_PROFILES_DIR": str(root / "config" / "profiles"),
            "OCTOWRIGHT_MACROS_DIR": str(root / "config" / "macros"),
            "OCTOWRIGHT_SCENARIOS_DIR": str(root / "config" / "scenarios"),
            "OCTOWRIGHT_CAPTURES_DIR": str(root / "cache" / "captures"),
            "OCTOWRIGHT_ADVISOR_STATE": str(root / "config" / "advisor.json"),
            "OCTOWRIGHT_UPGRADE_STATE": str(root / "config" / "upgrade.json"),
        }
    )
    return env


class ServeProcess:
    """A ``serve`` subprocess whose stdout and stderr are drained into queues."""

    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(  # nosec B603
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        self.stdout: queue.Queue[str | None] = queue.Queue()
        self.stderr: queue.Queue[str | None] = queue.Queue()
        self.stderr_seen: list[str] = []
        self.stdout_eof = threading.Event()
        self.stderr_eof = threading.Event()
        self._readers = [
            threading.Thread(target=_pump, args=(self.proc.stdout, self.stdout, self.stdout_eof), daemon=True),
            threading.Thread(target=_pump, args=(self.proc.stderr, self.stderr, self.stderr_eof), daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def close_stdin(self) -> None:
        assert self.proc.stdin is not None
        with contextlib.suppress(OSError):
            self.proc.stdin.close()

    def answer(self, request_id: str, *, timeout: float) -> dict[str, Any]:
        """The JSON-RPC response to *request_id*, skipping anything else on stdout."""
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                raw = self.stdout.get(timeout=remaining)
            except queue.Empty:
                break
            if raw is None:
                raise AssertionError(f"stdout closed before {request_id!r} was answered; {self.stderr_excerpt()}")
            with contextlib.suppress(json.JSONDecodeError):
                message = json.loads(raw)
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message
        raise TimeoutError(f"no answer to {request_id!r} within {timeout}s; {self.stderr_excerpt()}")

    def wait_for_stderr(self, marker: str, *, timeout: float) -> str:
        """Return the first stderr line containing *marker* (all lines are kept)."""
        for line in self.stderr_seen:
            if marker in line:
                return line
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                line = self.stderr.get(timeout=remaining)
            except queue.Empty:
                break
            if line is None:
                raise AssertionError(f"stderr closed before {marker!r} appeared; {self.stderr_excerpt()}")
            self.stderr_seen.append(line)
            if marker in line:
                return line
        raise TimeoutError(f"{marker!r} not seen within {timeout}s; {self.stderr_excerpt()}")

    def drain_stderr(self) -> list[str]:
        """Collect whatever stderr has arrived so far without waiting."""
        while True:
            try:
                line = self.stderr.get_nowait()
            except queue.Empty:
                return self.stderr_seen
            if line is not None:
                self.stderr_seen.append(line)

    def stderr_excerpt(self) -> str:
        return "stderr so far:\n" + "".join(self.drain_stderr())[-3000:]

    def kill(self) -> None:
        kill_process_tree(self.proc.pid)
        with contextlib.suppress(OSError):
            self.proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=10)


def _pump(stream: Any, sink: queue.Queue[str | None], eof: threading.Event) -> None:
    try:
        for line in iter(stream.readline, ""):
            sink.put(line)
    except (OSError, ValueError):
        pass  # the stream was closed under us during teardown
    finally:
        eof.set()
        sink.put(None)


def kill_process_tree(pid: int) -> None:
    """Kill *pid* and everything it started; a no-op if it is already gone.

    ``Popen.kill`` is ``TerminateProcess`` on Windows, which leaves children
    running, so the tree goes through ``taskkill /T``. On POSIX a detached
    daemon leads its own session, so its group is killed with it.
    """
    if sys.platform == "win32":
        subprocess.run(  # nosec B603 B607
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        return
    with contextlib.suppress(OSError):
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
            return
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
