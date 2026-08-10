# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live end-to-end: the leader emits progress heartbeats over the REAL wire.

The unit tests (``tests/test_progress_heartbeat.py``) prove the leader wrapper
calls ``send_progress_notification`` and that a returned progress frame re-arms the
follower deadline. This is the one thing they can't cover: that those pings
actually travel over the real streamable-HTTP ``/mcp`` transport during a tool
call, on the exact ``progressToken`` the caller supplied — the mechanism that
keeps a slow-but-alive tool call from tripping the follower's in-flight deadline
and surfacing as a spurious "Octowright disconnected".

We connect straight to the leader's ``/mcp`` (like the idempotency live smoke),
drive the daemon with a tiny ``OCTOWRIGHT_HEARTBEAT_INTERVAL_SECONDS`` so any real
tool call (``browser_launch`` takes well over the interval) emits at least one
ping, supply our own ``progressToken`` in ``_meta``, and assert progress frames
arrive on that token before the tool response.

Run with: ``uv run pytest -m live_browser tests/test_bridge_heartbeat_live.py``
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
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCNotification, JSONRPCRequest

pytestmark = pytest.mark.live_browser

_NO_ENGINE = (
    "executable doesn't exist",
    "missing x server",
    "no protocol specified",
    "playwright install",
)
_HEARTBEAT_INTERVAL = "0.3"  # tiny, so a ~1-3s browser_launch emits several pings


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
            # The behaviour under test: a fast heartbeat cadence so a real tool call
            # emits progress pings we can observe on the wire.
            "OCTOWRIGHT_HEARTBEAT_INTERVAL_SECONDS": _HEARTBEAT_INTERVAL,
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
    await write.send(SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method=method, params=params)))
    async for message in read:
        if isinstance(message, Exception):
            raise message
        root = message.message
        if getattr(root, "id", None) == request_id:
            return root
    raise RuntimeError("leader stream closed before response")


async def _notify(write: Any, method: str, params: dict[str, Any]) -> None:
    await write.send(SessionMessage(JSONRPCNotification(jsonrpc="2.0", method=method, params=params)))


async def _call_collecting_progress(
    write: Any, read: Any, request_id: int, params: dict[str, Any], progress_token: str
) -> tuple[Any, list[Any]]:
    """Send a tools/call carrying ``progress_token`` and return (response_root,
    progress_frames_on_that_token) — collecting every notifications/progress that
    arrives before the matching response id."""
    await write.send(SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=request_id, method="tools/call", params=params)))
    pings: list[Any] = []
    async for message in read:
        if isinstance(message, Exception):
            raise message
        root = message.message
        if isinstance(root, JSONRPCNotification) and root.method == "notifications/progress":
            token = (root.params or {}).get("progressToken")
            if token == progress_token:
                pings.append(root.params)
            continue
        if getattr(root, "id", None) == request_id:
            return root, pings
    raise RuntimeError("leader stream closed before response")


def _launch_result(root: Any) -> dict[str, Any]:
    if isinstance(root, JSONRPCError):
        raise RuntimeError(root.error.message)
    return json.loads(root.result["content"][0]["text"])


async def _run_heartbeat_check(mcp_url: str, token: str) -> None:
    headers = {"X-Octowright-Token": token} if token else None
    async with (
        create_mcp_http_client(headers=headers) as http_client,
        streamable_http_client(mcp_url, http_client=http_client) as (read, write),
    ):
        await _send_request(
            write,
            read,
            1,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "octowright-heartbeat-smoke", "version": "0"},
            },
        )
        await _notify(write, "notifications/initialized", {})

        progress_token = "owpt-heartbeat-live"
        root, pings = await _call_collecting_progress(
            write,
            read,
            2,
            {
                "name": "browser_launch",
                "arguments": {"url": "about:blank", "headed": False, "label": "hb"},
                "_meta": {"progressToken": progress_token},
            },
            progress_token,
        )
        result = _launch_result(root)
        if any(needle in json.dumps(result).lower() for needle in _NO_ENGINE):
            pytest.skip(f"no usable browser engine: {result}")

        # The load-bearing assertion: the leader streamed at least one progress
        # heartbeat on OUR token over the real transport during the call. That is
        # exactly what re-arms the follower's in-flight deadline and prevents the
        # slow-call-looks-like-a-disconnect failure.
        assert pings, "leader emitted no progress heartbeat on the wire during a real tool call"
        # progress is monotonically increasing (MCP requires it)
        values = [p.get("progress") for p in pings]
        assert values == sorted(values)


def test_leader_emits_progress_heartbeat_over_the_wire(tmp_path: Path) -> None:
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
    try:
        mcp_url = _await_leader(lock_path, port, timeout=45.0)
        leader_pid = _leader_pid(lock_path)
        from octowright import singleton as _sn

        lock_info = _sn.read_lock(lock_path)
        token = lock_info.token if lock_info is not None else ""
        anyio.run(_run_heartbeat_check, mcp_url, token)
    finally:
        if follower.stdin:
            with contextlib.suppress(OSError):
                follower.stdin.close()
        _terminate(follower.pid)
        if leader_pid is not None:
            _terminate(leader_pid)
