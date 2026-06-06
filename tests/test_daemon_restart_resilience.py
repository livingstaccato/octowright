# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""SKETCH — opt-in multi-process test documenting daemon-restart behavior.

What it actually proves (verified live, 2026-06-06): restarting the daemon
DISCONNECTS connected followers. `proxy_bridge.run_proxy` returns when the
leader goes away (its health watchdog tears the bridge down), then
`_serve_singleton` respawns a replacement daemon and *returns* — so the
follower process exits and its MCP client's stdio closes. The follower does
NOT transparently reconnect across a full restart; the client must reconnect
(a new session, for stdio clients). The respawn exists so the *next* client
finds a live leader quickly, not to keep the current one alive.

(The in-place reconnect that `proxy_supervisor` does is for a *transient*
leader stream drop while the leader process is still alive — a different,
narrower case this test does not cover.)

Skipped unless OCTOWRIGHT_RUN_DAEMON_IT=1 (slow; spawns real processes).

SAFETY: hermetic via env (own OCTOWRIGHT_LOCK_PATH + state dirs + ephemeral
port) so it never touches a real daemon, and every `restart` passes
`--keep-browsers` so the machine's real browsers are never reaped.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess  # nosec B404
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("OCTOWRIGHT_RUN_DAEMON_IT"),
        reason="opt-in multi-process integration test; set OCTOWRIGHT_RUN_DAEMON_IT=1 to run",
    ),
    pytest.mark.skipif(sys.platform == "win32", reason="restart signalling differs on Windows"),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _octowright_bin() -> str:
    return str(Path(sys.executable).parent / "octowright")


def _hermetic_env(tmp_path: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "OCTOWRIGHT_LOCK_PATH": str(tmp_path / "octowright.lock"),
            "OCTOWRIGHT_RECORDINGS": str(tmp_path / "recordings"),
            "OCTOWRIGHT_PROFILES_DIR": str(tmp_path / "profiles"),
            "OCTOWRIGHT_MACROS_DIR": str(tmp_path / "macros"),
            "OCTOWRIGHT_HTTP_HOST": "127.0.0.1",
            "OCTOWRIGHT_HTTP_PORT": str(port),
            "OCTOWRIGHT_IDLE_GRACE": "120",
            "OCTOWRIGHT_HEADLESS": "1",
        }
    )
    return env


async def _open_follower(stack: AsyncExitStack, params: StdioServerParameters) -> ClientSession:
    read, write = await stack.enter_async_context(stdio_client(params))
    session: ClientSession = await stack.enter_async_context(ClientSession(read, write))
    with anyio.fail_after(45):
        await session.initialize()
    return session


async def _status(session: ClientSession) -> dict[str, Any]:
    result = await session.call_tool("octowright_status", {})
    return json.loads(result.content[0].text)


async def _status_with_retry(session: ClientSession, *, attempts: int = 12, delay: float = 1.0) -> dict[str, Any]:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with anyio.fail_after(15):
                return await _status(session)
        except Exception as exc:
            last = exc
            await anyio.sleep(delay)
    raise AssertionError(f"status never recovered: {last!r}")


async def _session_is_dead(session: ClientSession, *, attempts: int = 10, delay: float = 0.5) -> bool:
    """True once a call on this session raises — i.e. the follower process exited."""
    for _ in range(attempts):
        try:
            await _status(session)
            await anyio.sleep(delay)
        except Exception:
            return True
    return False


def _restart(bin_: str, env: dict[str, str], cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    # --keep-browsers is mandatory: bare `restart` reaps orphan browsers scope="all".
    return subprocess.run(  # nosec B603
        [bin_, "restart", "--keep-browsers", "--timeout", "25", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.anyio
async def test_restart_disconnects_followers_then_a_fresh_client_reconnects(tmp_path: Path) -> None:
    port = _free_port()
    env = _hermetic_env(tmp_path, port)
    bin_ = _octowright_bin()
    cwd = str(tmp_path)
    params = StdioServerParameters(command=bin_, args=["serve"], cwd=cwd, env=env)

    old_pid: int
    async with AsyncExitStack() as stack:
        client_a = await _open_follower(stack, params)  # spawns a detached daemon, follows it
        before = await _status_with_retry(client_a)
        assert before["daemon"]["mode"] == "daemon", before["daemon"]
        old_pid = before["daemon"]["pid"]
        assert isinstance(old_pid, int)

        assert _restart(bin_, env, cwd).returncode == 0

        # VERIFIED behavior: the existing follower's session is now dead.
        assert await _session_is_dead(client_a), "follower unexpectedly survived a daemon restart"

    # Reconnect (new session) → the respawned daemon is live and answers.
    async with AsyncExitStack() as stack2:
        client_b = await _open_follower(stack2, params)
        after = await _status_with_retry(client_b)
        assert after["daemon"]["mode"] == "daemon"
        assert after["daemon"]["pid"] != old_pid, "expected a freshly respawned daemon"

    _restart(bin_, env, cwd, "--no-start")  # teardown
