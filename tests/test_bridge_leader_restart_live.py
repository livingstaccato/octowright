# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Faithful live regression for the bridge leader-restart survival.

The in-process proxy unit tests use mock streams that close cleanly. This drives
a REAL leader daemon (``octowright serve --no-singleton`` on an isolated port, so
the real :6286 daemon is never touched) through a REAL follower bridge with a REAL
MCP initialize handshake, then restarts the leader within the recovery window. The
client's session must survive — a ``tools/list`` issued across the gap is answered
by the respawned leader — and the recovery is metered ``bridge_leader_recovery
=recovered``.

Marked ``live_browser`` (heavy: spawns real daemons) so it's deselected from the
fast ``make test`` and runs only in the live suite. It launches no browser, so it
won't skip on a missing engine.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCRequest

pytestmark = pytest.mark.live_browser


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
            "OCTOWRIGHT_LOCK_PATH": str(root / "state" / "octowright.lock"),
            "OCTOWRIGHT_BRIDGE_STATE": str(root / "state" / "bridge-state.json"),
            "OCTOWRIGHT_RECORDINGS": str(root / "state" / "sessions"),
            "OCTOWRIGHT_SESSION_MANIFEST": str(root / "state" / "manifest.json"),
        }
    )
    return env


def _spawn_leader(octowright_bin: Path, env: dict[str, str], port: int) -> subprocess.Popen[str]:
    """Inline leader on ``port`` as a process we directly control — no detached
    daemon, no host-wide restart sweep. stdin is held open so its MCP stdio loop
    never EOFs."""
    return subprocess.Popen(  # nosec B603
        [str(octowright_bin), "serve", "--no-singleton", "--http-host", "127.0.0.1", "--http-port", str(port)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _wait_health(port: int, *, up: bool, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:  # nosec B310
                if resp.status == 200 and up:
                    return True
        except Exception:
            if not up:
                return True
        time.sleep(0.3)
    return False


def _terminate(proc: subprocess.Popen[Any]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        proc.send_signal(signal.SIGTERM)
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        proc.kill()


def _req(rid: int, method: str, params: dict[str, Any] | None = None) -> SessionMessage:
    return SessionMessage(JSONRPCRequest(jsonrpc="2.0", id=rid, method=method, params=params or {}))


async def _recv_id(recv: Any, want_id: int, timeout: float) -> Any:
    with anyio.move_on_after(timeout):
        async for msg in recv:
            if getattr(msg.message, "id", None) == want_id:
                return msg.message
    return None


@pytest.mark.anyio
async def test_follower_survives_leader_restart_and_meters_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    octowright_bin = Path(sys.executable).with_name("octowright")
    if not octowright_bin.exists():
        pytest.skip(f"octowright executable not found at {octowright_bin}")

    from octowright import proxy_runtime as runtime
    from tests._metric_recorders import RecordingCounter

    port = _free_port()
    env = _isolated_env(tmp_path, port)
    mcp_url = f"http://127.0.0.1:{port}/mcp/"
    health_url = f"http://127.0.0.1:{port}/api/health"

    recovery = RecordingCounter()
    monkeypatch.setattr(runtime, "_LEADER_RECOVERY", recovery)
    monkeypatch.setattr(runtime, "BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", 30.0)
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda _fallback: mcp_url)  # never touch the real lockfile

    bridge_io: dict[str, Any] = {}

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        in_send, in_recv = anyio.create_memory_object_stream[SessionMessage](64)
        out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](64)
        bridge_io["to_follower"], bridge_io["from_follower"] = in_send, out_recv
        yield (in_recv, out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)

    leader = await anyio.to_thread.run_sync(_spawn_leader, octowright_bin, env, port)
    leader2: subprocess.Popen[Any] | None = None
    try:
        assert await anyio.to_thread.run_sync(lambda: _wait_health(port, up=True)), "leader did not come up"

        async with anyio.create_task_group() as tg:

            async def _follower() -> None:
                with contextlib.suppress(Exception):
                    await runtime.run_supervised_proxy(
                        leader_mcp_url=mcp_url, health_url=health_url, heartbeat_interval=1.0, heartbeat_max_failures=3
                    )

            tg.start_soon(_follower)
            with anyio.fail_after(10):
                while "to_follower" not in bridge_io:
                    await anyio.sleep(0.05)

            # Real MCP initialize through the follower bridge.
            init = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}
            await bridge_io["to_follower"].send(_req(1, "initialize", init))
            assert await _recv_id(bridge_io["from_follower"], 1, 15.0) is not None, "no initialize response"

            # Restart the leader gracefully within the recovery window.
            await anyio.to_thread.run_sync(_terminate, leader)
            await anyio.to_thread.run_sync(lambda: _wait_health(port, up=False, timeout=8))
            await anyio.sleep(3)
            leader2 = await anyio.to_thread.run_sync(_spawn_leader, octowright_bin, env, port)
            assert await anyio.to_thread.run_sync(lambda: _wait_health(port, up=True)), "leader did not respawn"

            # The client's call crosses the restart and is answered by the new leader.
            await bridge_io["to_follower"].send(_req(2, "tools/list"))
            assert await _recv_id(bridge_io["from_follower"], 2, 25.0) is not None, (
                "session did not survive the restart"
            )
            assert "recovered" in recovery.attrs_for("outcome"), "recovery was not metered"
            tg.cancel_scope.cancel()
    finally:
        _terminate(leader)
        if leader2 is not None:
            _terminate(leader2)
