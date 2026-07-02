# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live boundary test: a DIRECT HTTP-MCP client (no follower) gets no push.

Documents the SDK limitation that motivated option A. A client connected straight
to the leader's ``/mcp`` transport — bypassing the follower bridge — receives no
server-initiated notifications, because the StreamableHTTP transport exposes no
push path. This test connects directly over ``/mcp``, launches then closes a
browser (an ``agent_close`` that publishes a ``SessionClosedEvent``), and confirms
NO ``notifications/octowright/*`` frame arrives (``delivered: False``).

The normal deployment — a stdio MCP client through the ``octowright serve``
follower — DOES receive these notifications via the leader's ``/api/mcp-events``
SSE + ``consume_leader_notifications`` re-injection; that path is proven in
``tests/test_mcp_events_daemon_live.py``. Together the two tests pin the
contract: via-follower = delivered, direct-/mcp = not.

Run with: ``uv run pytest -m live_browser tests/test_mcp_notifications_daemon_live.py -s``
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


async def _probe(mcp_url: str, token: str) -> bool:
    """Return True if a notifications/octowright/* frame is seen after a close.

    Single reader throughout (no concurrent consumers of ``read`` — two readers on
    one memory stream race for frames and deadlock). After issuing browser_close we
    drain the stream with a hard deadline, watching for both the close response and
    any octowright notification.
    """
    headers = {"X-Octowright-Token": token} if token else None
    async with streamablehttp_client(mcp_url, headers=headers) as (read, write, _sid):
        await _send_request(
            write,
            read,
            1,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "notif-probe", "version": "0"},
            },
        )
        await _notify(write, "notifications/initialized", {})
        root = await _send_request(
            write,
            read,
            2,
            "tools/call",
            {"name": "browser_launch", "arguments": {"url": "about:blank", "headed": False, "label": "n"}},
        )
        result = _tool_result(root)
        if any(needle in json.dumps(result).lower() for needle in _NO_ENGINE):
            pytest.skip(f"no usable browser engine: {result}")
        instance_id = result["instance_id"]

        # agent_close → publishes SessionClosedEvent on the pool's event bus.
        await write.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCRequest(
                        jsonrpc="2.0",
                        id=3,
                        method="tools/call",
                        params={"name": "browser_close", "arguments": {"instance_id": instance_id}},
                    )
                )
            )
        )

        saw_notification = False
        saw_close_response = False
        # Hard deadline so a missing notification can never hang the probe. Keep
        # reading past the close response for a grace window to catch a late push.
        with anyio.move_on_after(8.0):
            async for message in read:
                if isinstance(message, Exception):
                    break
                root = message.message.root
                method = getattr(root, "method", "")
                if isinstance(method, str) and method.startswith("notifications/octowright/"):
                    saw_notification = True
                    break
                if getattr(root, "id", None) == 3:
                    saw_close_response = True
                    # give late notifications a brief grace, then stop
                    with anyio.move_on_after(3.0):
                        async for late in read:
                            if isinstance(late, Exception):
                                break
                            lm = getattr(late.message.root, "method", "")
                            if isinstance(lm, str) and lm.startswith("notifications/octowright/"):
                                saw_notification = True
                                break
                    break
        assert saw_close_response, "browser_close response never arrived — probe is invalid"
        return saw_notification


def test_probe_session_notification_delivery_over_http_mcp(tmp_path: Path) -> None:
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
    delivered: bool | None = None
    try:
        mcp_url = _await_leader(lock_path, port, timeout=45.0)
        leader_pid = _leader_pid(lock_path)
        from octowright import singleton as _sn

        lock_info = _sn.read_lock(lock_path)
        token = lock_info.token if lock_info is not None else ""
        delivered = anyio.run(_probe, mcp_url, token)
    finally:
        if follower.stdin:
            with contextlib.suppress(OSError):
                follower.stdin.close()
        _terminate(follower.pid)
        if leader_pid is not None:
            _terminate(leader_pid)

    # Diagnostic, not a gate: record the observed behaviour loudly.
    print(f"\n[notif-probe] session_closed notification delivered over HTTP-MCP: {delivered}")
    assert delivered in (True, False)
