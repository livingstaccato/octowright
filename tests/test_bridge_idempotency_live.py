# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live end-to-end: leader-side idempotency prevents a double browser launch.

The real-browser confidence check for the bridge resume/dedup work. ``octowright
serve`` always runs as a FOLLOWER that spawns a leader daemon and OVERWRITES any
client idempotency key with its own per-request key (correct — the bridge owns
keys, and the SAME key recurs only on a resume). So to exercise the leader dedup
deterministically we connect straight to the leader's ``/mcp/`` endpoint and send
two ``browser_launch`` ``tools/call`` frames carrying the SAME
``octowrightIdempotencyKey`` in ``_meta`` — exactly what a resumed forward looks
like on the wire. The leader must return the SAME instance and launch only ONE
real browser; a DIFFERENT key must launch a second.

The bridge-side resume/budget/deadline/key-injection behaviour is covered
deterministically in ``tests/test_proxy_supervisor.py``; this fills the one gap
those can't — that the leader dedup actually suppresses a second real launch.

Run with: ``uv run pytest -m live_browser tests/test_bridge_idempotency_live.py``
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
from mcp.types import JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest

pytestmark = pytest.mark.live_browser

_NO_ENGINE = (
    "executable doesn't exist",
    "missing x server",
    "no protocol specified",
    "playwright install",
)


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
            "OCTOWRIGHT_IDEMPOTENCY": "1",  # the behaviour under test (also the default)
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
    """Wait for the spawned leader daemon to be healthy; return its mcp_url."""
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
    """Send one JSON-RPC request to the leader and return the matching response root."""
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


def _launch_result(root: Any) -> dict[str, Any]:
    if isinstance(root, JSONRPCError):
        raise RuntimeError(root.error.message)
    return json.loads(root.result["content"][0]["text"])


async def _run_dedup_check(mcp_url: str, token: str) -> None:
    # The leader's /mcp now requires the lockfile capability token; this direct
    # client reads it from the (owner-only) lockfile and presents it, exactly as
    # the follower bridge does.
    headers = {"X-Octowright-Token": token} if token else None
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _get_sid):
        await _send_request(
            write,
            read,
            1,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "octowright-idempotency-smoke", "version": "0"},
            },
        )
        await _notify(write, "notifications/initialized", {})

        # First launch under key A.
        root = await _send_request(
            write,
            read,
            2,
            "tools/call",
            {
                "name": "browser_launch",
                "arguments": {"url": "about:blank", "headed": False, "label": "idem"},
                "_meta": {"octowrightIdempotencyKey": "owk-live-smoke-a"},
            },
        )
        first = _launch_result(root)
        if any(needle in json.dumps(first).lower() for needle in _NO_ENGINE):
            pytest.skip(f"no usable browser engine: {first}")
        instance_a = first["instance_id"]

        # Re-send the SAME key (a bridge resume on the wire) → cached launch, no second browser.
        root = await _send_request(
            write,
            read,
            3,
            "tools/call",
            {
                "name": "browser_launch",
                "arguments": {"url": "about:blank", "headed": False, "label": "idem"},
                "_meta": {"octowrightIdempotencyKey": "owk-live-smoke-a"},
            },
        )
        assert _launch_result(root)["instance_id"] == instance_a, "same idempotency key launched a second browser"

        root = await _send_request(write, read, 4, "tools/call", {"name": "browser_list", "arguments": {}})
        assert _launch_result(root)["count"] == 1, "leader should hold exactly one browser after a deduped re-send"

        # Control: a DIFFERENT key is a distinct logical call → a real second browser.
        root = await _send_request(
            write,
            read,
            5,
            "tools/call",
            {
                "name": "browser_launch",
                "arguments": {"url": "about:blank", "headed": False, "label": "idem2"},
                "_meta": {"octowrightIdempotencyKey": "owk-live-smoke-b"},
            },
        )
        instance_b = _launch_result(root)["instance_id"]
        assert instance_b != instance_a

        root = await _send_request(write, read, 6, "tools/call", {"name": "browser_list", "arguments": {}})
        assert _launch_result(root)["count"] == 2, "distinct keys should yield two browsers"


def test_idempotent_browser_launch_does_not_double_execute(tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    octowright_bin = Path(sys.executable).with_name("octowright")
    if not octowright_bin.exists():
        pytest.skip(f"octowright executable not found at {octowright_bin}")

    port = _free_port()
    env = _isolated_env(tmp_path, port)
    lock_path = Path(env["OCTOWRIGHT_LOCK_PATH"])
    # `octowright serve` is a follower that spawns the leader daemon we talk to.
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
        anyio.run(_run_dedup_check, mcp_url, token)
    finally:
        if follower.stdin:
            with contextlib.suppress(OSError):
                follower.stdin.close()
        _terminate(follower.pid)
        if leader_pid is not None:
            _terminate(leader_pid)
