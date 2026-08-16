# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Opt-in multi-process tests documenting the bridge's two distinct drop modes.

Both verified live (2026-06-06):

1. **Full restart / leader gone** (`test_restart_disconnects_*`): `octowright
   restart` (or any leader kill/crash/idle-exit) makes `proxy_bridge.run_proxy`
   return — `_serve_singleton` respawns a replacement daemon and *returns*, so
   the follower process exits and its MCP client's stdio closes. The client must
   reconnect (a new session, for stdio clients). The respawn just lets the *next*
   client find a live leader quickly.

2. **Transient drop, leader alive** (`test_transient_drop_*`): when the MCP
   stream drops but `/api/health` keeps answering, `proxy_supervisor` reconnects
   in place — the client session SURVIVES and the daemon pid is unchanged. This
   is induced with a byte-level TCP proxy whose connections are force-closed
   while the daemon keeps running.

Skipped unless OCTOWRIGHT_RUN_DAEMON_IT=1 (slow; spawns real processes).

SAFETY: hermetic via env (own OCTOWRIGHT_LOCK_PATH + state dirs + ephemeral
port) so it never touches a real daemon, and every `restart` passes
`--keep-browsers` so the machine's real browsers are never reaped. The
transient test never kills the daemon, so it reaps nothing.
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
import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from octowright import singleton
from octowright.singleton import LeaderInfo

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
            # Without this, the detached daemon this spawns opens its stderr via
            # daemonize._open_daemon_log(), which resolves user_state_dir() from
            # the INHERITED real environment: every run leaked a real
            # "octowright.http.listening ... port=<random>" line into the
            # developer's actual ~/.local/state/octowright/logs/octowright-daemon.log
            # (see tests/test_daemonize.py's identical fix). Also isolates
            # bridge-state.json so this test's follower snapshots (random
            # ephemeral port, ConnectError at teardown) don't pollute the real
            # daemon's bridge summary that octowright_status() reports.
            "XDG_STATE_HOME": str(tmp_path),
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


async def _wait_health(port: int, timeout: float = 40.0) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        with anyio.fail_after(timeout):
            while True:
                try:
                    if (await client.get(f"http://127.0.0.1:{port}/api/health")).status_code == 200:
                        return
                except Exception:
                    pass
                await anyio.sleep(0.3)


class _Proxy:
    """A byte-level TCP proxy whose live connections can be force-closed, to
    induce a transient stream drop without touching the leader process."""

    def __init__(self, target_port: int) -> None:
        self._target = target_port
        self.conns: set[tuple[Any, Any]] = set()

    async def _pump(self, src: Any, dst: Any) -> None:
        try:
            while True:
                await dst.send(await src.receive())
        except Exception:
            pass

    async def handle(self, client: Any) -> None:
        try:
            leader = await anyio.connect_tcp("127.0.0.1", self._target)
        except Exception:
            await client.aclose()
            return
        pair = (client, leader)
        self.conns.add(pair)
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(self._pump, client, leader)
                tg.start_soon(self._pump, leader, client)
        finally:
            self.conns.discard(pair)

    async def drop_all(self) -> int:
        pairs = list(self.conns)
        for client, leader in pairs:
            for stream in (client, leader):
                try:
                    await stream.aclose()
                except Exception:
                    pass
        self.conns.clear()
        return len(pairs)


@pytest.mark.anyio
async def test_transient_drop_reconnects_in_place_while_leader_stays_alive(tmp_path: Path) -> None:
    leader_port = _free_port()
    proxy_port = _free_port()
    env = _hermetic_env(tmp_path, leader_port)
    bin_ = _octowright_bin()
    cwd = str(tmp_path)
    lockpath = tmp_path / "octowright.lock"

    daemon = subprocess.Popen(  # nosec B603
        [bin_, "serve", "--daemon-mode", "--http-host", "127.0.0.1", "--http-port", str(leader_port)],
        env=env,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await _wait_health(leader_port)
        info = singleton.read_lock(lockpath)
        assert info is not None
        daemon_pid = info.pid

        proxy = _Proxy(leader_port)
        listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=proxy_port)
        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.serve, proxy.handle)
            # Route the follower through the proxy (lockfile stays valid: pid alive).
            singleton.write_lock(
                LeaderInfo(
                    pid=info.pid,
                    http_host="127.0.0.1",
                    http_port=proxy_port,
                    mcp_url=f"http://127.0.0.1:{proxy_port}/mcp/",
                    started_at=info.started_at,
                ),
                lockpath,
            )
            params = StdioServerParameters(command=bin_, args=["serve"], cwd=cwd, env=env)
            async with stdio_client(params) as (r, w), ClientSession(r, w) as client_a:
                with anyio.fail_after(45):
                    await client_a.initialize()
                assert (await _status_with_retry(client_a))["daemon"]["pid"] == daemon_pid

                assert await proxy.drop_all() > 0, "expected live proxied connections to drop"

                # Leader still alive → supervisor reconnects in place; session survives.
                after = await _status_with_retry(client_a)
                assert after["daemon"]["pid"] == daemon_pid, "leader must NOT have restarted"
            tg.cancel_scope.cancel()
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=20)
        except Exception:
            daemon.kill()
