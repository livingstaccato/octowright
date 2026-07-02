# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live end-to-end: proactive notifications reach the client in daemon mode.

The decisive proof for option A. Against a real detached-daemon leader, we run
the follower's ``consume_leader_notifications`` (which streams the leader's
``/api/mcp-events`` SSE and injects frames into a local write) while, over a
separate ``/mcp`` client, we launch and close a browser. Closing publishes a
``SessionClosedEvent`` on the leader's pool bus; the assertion is that the
notification arrives at the collector — i.e. the HTTP-MCP-transport awareness gap
proven by ``test_mcp_notifications_daemon_live.py`` is now closed for clients that
connect through the follower bridge (the normal case).

Run with: ``uv run pytest -m live_browser tests/test_mcp_events_daemon_live.py``
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import anyio
import pytest
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest

from octowright import proxy_runtime
from octowright.proxy_runtime import consume_leader_notifications

pytestmark = pytest.mark.live_browser

_NO_ENGINE = ("executable doesn't exist", "missing x server", "no protocol specified", "playwright install")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _isolated_env(root: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    # Isolate the state dir so the detached daemon writes its log under the test
    # tmpdir (user_state_dir honors XDG_STATE_HOME) instead of polluting the
    # user's shared ~/.local/state/octowright/logs/octowright-daemon.log.
    env["XDG_STATE_HOME"] = str(root)
    env.update(
        {
            "OCTOWRIGHT_HEADLESS": "1",
            "OCTOWRIGHT_HTTP_HOST": "127.0.0.1",
            "OCTOWRIGHT_HTTP_PORT": str(port),
            "OCTOWRIGHT_IDLE_GRACE": "120",
            "OCTOWRIGHT_LOCK_PATH": str(root / "state" / "octowright.lock"),
            "OCTOWRIGHT_BRIDGE_STATE": str(root / "state" / "bridge-state.json"),
            "OCTOWRIGHT_RECORDINGS": str(root / "state" / "sessions"),
            "OCTOWRIGHT_SESSION_MANIFEST": str(root / "state" / "session-manifest.json"),
            "OCTOWRIGHT_PROFILES_DIR": str(root / "config" / "profiles"),
            "OCTOWRIGHT_MACROS_DIR": str(root / "config" / "macros"),
            "OCTOWRIGHT_SCENARIOS_DIR": str(root / "config" / "scenarios"),
            "OCTOWRIGHT_CAPTURES_DIR": str(root / "cache" / "captures"),
            "OCTOWRIGHT_ADVISOR_STATE": str(root / "config" / "advisor.json"),
        }
    )
    return env


def _terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGKILL)


def _await_leader(lock_path: Path, port: int, *, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            mcp_url = lock.get("mcp_url")
            health_port = lock.get("http_port", port)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            mcp_url, health_port = None, port
        if isinstance(mcp_url, str):
            try:
                with urlopen(f"http://127.0.0.1:{health_port}/api/health", timeout=0.5) as resp:  # nosec B310
                    if resp.status == 200:
                        return mcp_url
            except (OSError, URLError):
                pass
        time.sleep(0.1)
    raise TimeoutError("leader daemon did not become healthy")


def _leader_pid(lock_path: Path) -> int | None:
    try:
        pid = json.loads(lock_path.read_text(encoding="utf-8")).get("pid")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return pid if isinstance(pid, int) else None


async def _send_request(write: Any, read: Any, request_id: int, method: str, params: dict[str, Any]) -> Any:
    await write.send(
        SessionMessage(JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=params)))
    )
    async for message in read:
        if isinstance(message, Exception):
            raise message
        root = message.message.root
        if getattr(root, "id", None) == request_id:
            return root
    raise RuntimeError("leader stream closed before response")


async def _notify(write: Any, method: str, params: dict[str, Any]) -> None:
    await write.send(
        SessionMessage(JSONRPCMessage(root=JSONRPCNotification(jsonrpc="2.0", method=method, params=params)))
    )


def _tool_result(root: Any) -> dict[str, Any]:
    return json.loads(root.result["content"][0]["text"])


class _Collector:
    def __init__(self, done: anyio.Event) -> None:
        self.methods: list[str] = []
        self._done = done

    async def send(self, message: Any) -> None:
        method = getattr(message.message.root, "method", "")
        self.methods.append(method)
        if method == "notifications/octowright/session_closed":
            self._done.set()


async def _run(mcp_url: str, token: str) -> dict[str, Any]:
    # In production the FOLLOWER process holds the isolated lockfile env, so
    # consume_leader_notifications' resolve_leader_url() finds the right leader.
    # This test process does not, so pin resolution to the known test leader —
    # otherwise it would resolve to whatever daemon owns the default lockfile.
    proxy_runtime.resolve_leader_url = lambda _fallback: mcp_url  # type: ignore[assignment]
    headers = {"X-Octowright-Token": token} if token else None
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _sid):
        await _send_request(
            write,
            read,
            1,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "evt-e2e", "version": "0"}},
        )
        await _notify(write, "notifications/initialized", {})
        root = await _send_request(
            write,
            read,
            2,
            "tools/call",
            {"name": "browser_launch", "arguments": {"url": "about:blank", "headed": False, "label": "e2e"}},
        )
        result = _tool_result(root)
        if any(needle in json.dumps(result).lower() for needle in _NO_ENGINE):
            return {"skip": True}
        instance_id = result["instance_id"]

        done = anyio.Event()
        collector = _Collector(done)
        async with anyio.create_task_group() as tg:
            # Follower notification consumer against the real leader SSE.
            tg.start_soon(consume_leader_notifications, mcp_url, collector)
            await anyio.sleep(1.5)  # let the SSE connect + subscribe to the bus
            # agent_close publishes SessionClosedEvent on the leader's pool bus.
            await _send_request(
                write, read, 3, "tools/call", {"name": "browser_close", "arguments": {"instance_id": instance_id}}
            )
            with anyio.move_on_after(8.0):
                await done.wait()
            tg.cancel_scope.cancel()
        return {"skip": False, "delivered": done.is_set(), "methods": collector.methods}


def test_notifications_delivered_via_follower_bridge_in_daemon_mode(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    octowright_bin = Path(sys.executable).with_name("octowright")
    if not octowright_bin.exists():
        pytest.skip(f"octowright executable not found at {octowright_bin}")

    port = _free_port()
    env = _isolated_env(tmp_path, port)
    lock_path = Path(env["OCTOWRIGHT_LOCK_PATH"])
    follower = subprocess.Popen(  # nosec B603
        [str(octowright_bin), "serve", "--idle-grace", "120"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    leader_pid: int | None = None
    outcome: dict[str, Any] = {}
    try:
        mcp_url = _await_leader(lock_path, port, timeout=45.0)
        leader_pid = _leader_pid(lock_path)
        from octowright import singleton as _sn

        lock_info = _sn.read_lock(lock_path)
        token = lock_info.token if lock_info is not None else ""
        outcome = anyio.run(_run, mcp_url, token)
    finally:
        if follower.stdin:
            with contextlib.suppress(OSError):
                follower.stdin.close()
        _terminate(follower.pid)
        if leader_pid is not None:
            _terminate(leader_pid)

    if outcome.get("skip"):
        pytest.skip("no usable browser engine")
    assert outcome["delivered"], (
        f"session_closed notification not delivered over the follower bridge; saw {outcome.get('methods')}"
    )
