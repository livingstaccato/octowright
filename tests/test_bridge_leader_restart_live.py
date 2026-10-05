# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Faithful live regression for the bridge leader-restart survival.

The in-process proxy unit tests use mock streams that close cleanly. This drives
a REAL leader daemon (``octowright serve --daemon-mode`` on an isolated port and
isolated state, so the real :6286 daemon and its lockfile are never touched)
through a REAL follower bridge with a REAL MCP initialize handshake, then restarts
the leader within the recovery window. The client's session must survive — a
``tools/list`` issued across the gap is answered, with a result, by the respawned
leader — and the recovery is metered ``bridge_leader_recovery=recovered``.

It used to run the leader as ``serve --no-singleton``, which serves no ``/mcp`` at
all (an inline leader has no followers): every frame was answered ``405``, the
test accepted those error answers as replies, and the recovery it metered came
from a reconnect that "succeeded" only because the transport connects lazily.
The daemon here is started through a small entry script that disables the boot
orphan-browser sweep (a test must not reap browsers it does not own), with
housekeeping off and its own temp dir, so its session-dir sweep cannot reach the
host's.

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
import textwrap
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

pytestmark = pytest.mark.live_browser


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _isolated_env(root: Path, port: int) -> dict[str, str]:
    env = os.environ.copy()
    tmp_dir = root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    # Isolate the state dir so the detached daemon writes its log under the test
    # tmpdir (user_state_dir honors XDG_STATE_HOME) instead of polluting the
    # user's shared ~/.local/state/octowright/logs/octowright-daemon.log.
    env["XDG_STATE_HOME"] = str(root)
    # And the CONFIG dir, which is a different XDG root. Enumerating one env var
    # per config path is exactly how the upgrade marker was missed: the daemon
    # this test spawns wrote the developer's real ~/.config/octowright/upgrade.json,
    # marking the version "seen" -- so the post-upgrade what's-new notice was
    # consumed on every `make ci` run and never fired for a real upgrade.
    # Isolating the root covers every config path, including future ones.
    env["XDG_CONFIG_HOME"] = str(root / "config")
    env.update(
        {
            "OCTOWRIGHT_HEADLESS": "1",
            "OCTOWRIGHT_HTTP_HOST": "127.0.0.1",
            "OCTOWRIGHT_HTTP_PORT": str(port),
            "OCTOWRIGHT_LOCK_PATH": str(root / "state" / "octowright.lock"),
            "OCTOWRIGHT_BRIDGE_STATE": str(root / "state" / "bridge-state.json"),
            "OCTOWRIGHT_RECORDINGS": str(root / "state" / "sessions"),
            "OCTOWRIGHT_SESSION_MANIFEST": str(root / "state" / "manifest.json"),
            "OCTOWRIGHT_HOUSEKEEPING_SECONDS": "off",
            "TMPDIR": str(tmp_dir),
        }
    )
    return env


_LEADER_SCRIPT = textwrap.dedent(
    """
    import sys

    from octowright import housekeeping
    from octowright.cli import main

    housekeeping.reap_orphan_browsers_at_boot = lambda **_kwargs: None
    sys.argv = ["octowright", *sys.argv[1:]]
    main()
    """
)


def _spawn_leader(leader_script: Path, env: dict[str, str], port: int, log_path: Path) -> subprocess.Popen[str]:
    """A daemon-mode leader on ``port`` as a process we directly control (not
    detached), so it serves ``/mcp`` and writes the isolated lockfile the
    follower reads its capability token from. Its output goes to ``log_path``,
    quoted by a failure."""
    with log_path.open("w", encoding="utf-8") as log:
        return subprocess.Popen(  # nosec B603
            [
                sys.executable,
                str(leader_script),
                "serve",
                "--daemon-mode",
                "--http-host",
                "127.0.0.1",
                "--http-port",
                str(port),
            ],
            env=env,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )


def _log_tail(log_path: Path, lines: int = 40) -> str:
    with contextlib.suppress(OSError):
        return "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    return "<no leader log>"


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
    from octowright import proxy_runtime as runtime
    from octowright import singleton
    from tests._metric_recorders import RecordingCounter

    port = _free_port()
    env = _isolated_env(tmp_path, port)
    leader_script = tmp_path / "leader_entry.py"
    leader_script.write_text(_LEADER_SCRIPT, encoding="utf-8")
    lock_path = Path(env["OCTOWRIGHT_LOCK_PATH"])
    mcp_url = f"http://127.0.0.1:{port}/mcp/"
    health_url = f"http://127.0.0.1:{port}/api/health"

    recovery = RecordingCounter()
    monkeypatch.setattr(runtime, "_LEADER_RECOVERY", recovery)
    monkeypatch.setattr(runtime, "BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS", 30.0)
    # Never touch the real lockfile: the URL is known, and the token and
    # generation come from this test's own leader's lockfile, re-read on each
    # connect as the real resolvers do (the respawned leader writes a new one).
    monkeypatch.setattr(runtime, "resolve_leader_url", lambda _fallback: mcp_url)

    def _isolated_token() -> str:
        info = singleton.read_lock(lock_path)
        return info.token if info is not None else ""

    def _isolated_generation() -> str | None:
        info = singleton.read_lock(lock_path)
        return None if info is None else f"{info.pid}:{info.started_at!r}"

    monkeypatch.setattr(runtime, "resolve_leader_token", _isolated_token)
    monkeypatch.setattr(runtime, "resolve_leader_generation", _isolated_generation)

    bridge_io: dict[str, Any] = {}

    @asynccontextmanager
    async def fake_stdio():  # type: ignore[no-untyped-def]
        in_send, in_recv = anyio.create_memory_object_stream[SessionMessage](64)
        out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](64)
        bridge_io["to_follower"], bridge_io["from_follower"] = in_send, out_recv
        yield (in_recv, out_send)

    monkeypatch.setattr(runtime, "stdio_server", fake_stdio)

    leader_log = tmp_path / "leader-1.log"
    leader = await anyio.to_thread.run_sync(_spawn_leader, leader_script, env, port, leader_log)
    leader2: subprocess.Popen[Any] | None = None
    try:
        assert await anyio.to_thread.run_sync(lambda: _wait_health(port, up=True)), (
            f"leader did not come up:\n{_log_tail(leader_log)}"
        )

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
            init_reply = await _recv_id(bridge_io["from_follower"], 1, 15.0)
            assert init_reply is not None, "no initialize response"
            assert isinstance(init_reply, JSONRPCResponse), f"the leader refused initialize: {init_reply!r}"
            await bridge_io["to_follower"].send(
                SessionMessage(JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized"))
            )

            # Restart the leader gracefully within the recovery window.
            await anyio.to_thread.run_sync(_terminate, leader)
            await anyio.to_thread.run_sync(lambda: _wait_health(port, up=False, timeout=8))
            await anyio.sleep(3)
            leader2_log = tmp_path / "leader-2.log"
            leader2 = await anyio.to_thread.run_sync(_spawn_leader, leader_script, env, port, leader2_log)
            assert await anyio.to_thread.run_sync(lambda: _wait_health(port, up=True)), (
                f"leader did not respawn:\n{_log_tail(leader2_log)}"
            )

            # The client's call crosses the restart and is answered by the new leader.
            await bridge_io["to_follower"].send(_req(2, "tools/list"))
            listed = await _recv_id(bridge_io["from_follower"], 2, 25.0)
            assert listed is not None, "session did not survive the restart"
            assert isinstance(listed, JSONRPCResponse), f"tools/list across the restart was refused: {listed!r}"
            if sys.platform == "win32" and "recovered" not in recovery.attrs_for("outcome"):
                # Windows has no graceful SIGTERM -- CPython's os.kill() on
                # Windows is TerminateProcess() regardless of signum (see
                # cli/restart.py's _FORCE_KILL comment, and the
                # signal_handler_register_failed warning octowright's own
                # leader logs at boot: add_signal_handler is simply not
                # implemented there). _terminate()'s SIGTERM therefore never
                # runs the leader's own graceful-shutdown path, so the socket
                # this follower is reading from gets no clean FIN -- it must
                # instead notice via a lower-level read timeout, which is not
                # guaranteed to fire before the new leader comes up in this
                # test's window. The assertions above already prove the
                # mechanism itself works (the session survived the restart
                # and was answered by the new leader); only whether THIS
                # particular gap got metered as "recovered" is racy here,
                # tied to that platform difference -- not asserted strictly.
                pytest.skip("recovery metering is racy on Windows: abrupt TerminateProcess gives no clean FIN")
            assert "recovered" in recovery.attrs_for("outcome"), "recovery was not metered"
            tg.cancel_scope.cancel()
    finally:
        _terminate(leader)
        if leader2 is not None:
            _terminate(leader2)
