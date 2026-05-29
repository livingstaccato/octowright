#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import socket
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, TextIO
from urllib.error import URLError
from urllib.request import urlopen


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def smoke_env(root: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "OCTOWRIGHT_HTTP_HOST": "127.0.0.1",
            "OCTOWRIGHT_HTTP_PORT": str(port),
            "OCTOWRIGHT_HEADLESS": "1",
            "OCTOWRIGHT_IDLE_GRACE": "120",
            "OCTOWRIGHT_LOCK_PATH": str(root / "state" / "octowright.lock"),
            "OCTOWRIGHT_BRIDGE_STATE": str(root / "state" / "bridge-state.json"),
            "OCTOWRIGHT_BRIDGE_HEALTH_INTERVAL_SECONDS": "0.5",
            "OCTOWRIGHT_BRIDGE_HEALTH_MAX_FAILURES": "2",
            "OCTOWRIGHT_BRIDGE_REQUEST_TIMEOUT_SECONDS": "2",
            "OCTOWRIGHT_RECORDINGS": str(root / "state" / "sessions"),
            "OCTOWRIGHT_SESSION_MANIFEST": str(root / "state" / "session-manifest.json"),
            "OCTOWRIGHT_PROFILES_DIR": str(root / "config" / "profiles"),
            "OCTOWRIGHT_MACROS_DIR": str(root / "config" / "macros"),
            "OCTOWRIGHT_SCENARIOS_DIR": str(root / "config" / "scenarios"),
            "OCTOWRIGHT_CAPTURES_DIR": str(root / "cache" / "captures"),
            "OCTOWRIGHT_ADVISOR_STATE": str(root / "config" / "advisor.json"),
            "OCTOWRIGHT_PROFILE": "core",
        }
    )
    return env


def status_from_text(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError("octowright_status returned a non-object payload")
    return payload


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_pid(pid: int, *, timeout: float = 5.0) -> None:
    if pid <= 0 or not pid_is_alive(pid):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            return
        time.sleep(0.05)
    if pid_is_alive(pid):
        os.kill(pid, signal.SIGKILL)


def health_ok(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def wait_for_health_down(port: int, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not health_ok(port):
            return
        time.sleep(0.1)
    raise TimeoutError("leader health endpoint stayed up after kill")


def read_lock_pid(lock_path: Path) -> int | None:
    raw = read_lock(lock_path)
    pid = raw.get("pid") if raw is not None else None
    return pid if isinstance(pid, int) else None


def read_lock(lock_path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(lock_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def wait_for_replacement(lock_path: Path, *, old_pid: int, port: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lock = read_lock(lock_path)
        pid = lock.get("pid") if lock is not None else None
        health_port = lock.get("http_port") if lock is not None else port
        if isinstance(pid, int) and pid != old_pid and isinstance(health_port, int) and health_ok(health_port):
            return pid
        time.sleep(0.1)
    raise TimeoutError("replacement daemon did not become healthy before timeout")


def _reader_thread(stream: TextIO, out: queue.Queue[str | None], *, echo: bool = False) -> None:
    try:
        for line in stream:
            if echo:
                print(line, end="", file=sys.stderr)
            else:
                out.put(line)
    finally:
        out.put(None)


class RawMcpClient:
    def __init__(self, command: str, args: list[str], *, cwd: str, env: dict[str, str], timeout: float) -> None:
        self.timeout = timeout
        self.proc = subprocess.Popen(  # nosec B603
            [command, *args],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self.proc.stdin is None or self.proc.stdout is None or self.proc.stderr is None:
            raise RuntimeError("failed to open stdio pipes")
        self.stdin = self.proc.stdin
        self.stdout_queue: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=_reader_thread, args=(self.proc.stdout, self.stdout_queue), daemon=True).start()
        threading.Thread(
            target=_reader_thread, args=(self.proc.stderr, queue.Queue()), kwargs={"echo": True}, daemon=True
        ).start()

    def close(self) -> None:
        try:
            self.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def send(self, message: dict[str, Any]) -> None:
        self.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.stdin.flush()

    def receive(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                line = self.stdout_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("octowright stdio closed before response")
            payload = json.loads(line)
            if payload.get("id") == request_id:
                return payload
        raise TimeoutError(f"timed out waiting for JSON-RPC response {request_id}")

    def initialize(self) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "octowright-bridge-smoke", "version": "0"},
                },
            }
        )
        self.receive(1)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call_status(self, request_id: int = 2) -> dict[str, Any]:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": "octowright_status", "arguments": {}},
            }
        )
        response = self.receive(request_id)
        if "error" in response:
            raise RuntimeError(response["error"])
        text = response["result"]["content"][0]["text"]
        return status_from_text(text)


def run_smoke(args: argparse.Namespace) -> int:
    port = args.port or free_tcp_port()
    command = args.command
    serve_args = ["serve", "--idle-grace", "120"]

    with tempfile.TemporaryDirectory(prefix="octowright-bridge-smoke-") as tmp:
        root = Path(tmp)
        env = smoke_env(root, port)
        replacement_pid: int | None = None
        client = RawMcpClient(command, serve_args, cwd=args.cwd, env=env, timeout=args.timeout)

        try:
            client.initialize()
            status = client.call_status()
            daemon_pid = status["daemon"]["pid"]
            if not isinstance(daemon_pid, int):
                raise RuntimeError(f"expected daemon pid, got {daemon_pid!r}")
            print(f"leader_pid={daemon_pid}")
            terminate_pid(daemon_pid)
            wait_for_health_down(port, timeout=min(args.timeout, 10.0))
            print("leader_killed=true")
            try:
                client.call_status(3)
            except Exception as exc:
                print(f"post_kill_request_error={type(exc).__name__}")
            replacement_pid = wait_for_replacement(
                Path(env["OCTOWRIGHT_LOCK_PATH"]),
                old_pid=daemon_pid,
                port=port,
                timeout=args.timeout,
            )
            print(f"replacement_pid={replacement_pid}")
        finally:
            client.close()
            if replacement_pid is not None and not args.keep_daemon:
                terminate_pid(replacement_pid)
                print("replacement_cleaned=true")

    print("bridge_dead_leader_smoke=pass")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify follower recovery after killing an isolated leader daemon.")
    parser.add_argument("--command", default=".venv/bin/octowright", help="octowright executable to run")
    parser.add_argument("--cwd", default=".", help="working directory for the smoke subprocess")
    parser.add_argument("--port", type=int, default=0, help="localhost port to use; default chooses a free port")
    parser.add_argument("--timeout", type=float, default=45.0, help="seconds to wait for each smoke phase")
    parser.add_argument("--keep-daemon", action="store_true", help="leave the replacement daemon running")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run_smoke(args)
    except Exception as exc:
        print(f"bridge_dead_leader_smoke=fail error={exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
