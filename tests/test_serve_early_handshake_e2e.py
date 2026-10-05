# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""End to end: a real ``octowright serve`` connects while its election stalls.

The follower opens stdio before it elects a leader, so a client's
``initialize`` is answered while the election is still running -- however long
that takes -- and the first tool call is what waits. Driven through the real
CLI in a subprocess over real stdio, with the election replaced by one that
announces itself, stalls, and then reports that no daemon came up: the process
then falls back to an inline leader, served through the bridge that already
owns stdio, and answers ``tools/list`` from the real tool registry.

Hermetic: every state, config, lock and recordings path is under the test's
tmp dir, the HTTP port is a free one (never the canonical 6286/6287), the boot
and periodic orphan sweeps are off, and no daemon is spawned.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import socket
import subprocess  # nosec B404
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

_ELECTION_STALL_SECONDS = 6.0

_SCRIPT = textwrap.dedent(
    f"""
    import asyncio
    import sys

    from octowright import housekeeping
    from octowright.cli import _leader_election as election
    from octowright.cli import main

    async def stalled_election(**_kwargs):
        print("ELECTION-STARTED", file=sys.stderr, flush=True)
        await asyncio.sleep({_ELECTION_STALL_SECONDS})
        print("ELECTION-DONE", file=sys.stderr, flush=True)
        return None  # no daemon answered: fall back to an inline leader

    election.elect_leader = stalled_election
    housekeeping.reap_orphan_browsers_at_boot = lambda **_kwargs: None
    sys.argv = ["octowright", "serve", "--http-port", sys.argv[1], "--keep-alive"]
    main()
    """
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _isolated_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("OCTOWRIGHT_PLUGINS", None)
    env["XDG_STATE_HOME"] = str(root / "state")
    env["XDG_CONFIG_HOME"] = str(root / "config")
    env["XDG_CACHE_HOME"] = str(root / "cache")
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


def _pump(stream: Any, sink: queue.Queue[str]) -> None:
    for line in iter(stream.readline, ""):
        sink.put(line)


def _next_json(lines: queue.Queue[str], *, timeout: float, request_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            raw = lines.get(timeout=remaining)
        except queue.Empty:
            break
        with contextlib.suppress(json.JSONDecodeError):
            message = json.loads(raw)
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
    raise TimeoutError(f"no answer to {request_id!r} within {timeout}s")


def _wait_for_line(lines: queue.Queue[str], marker: str, *, timeout: float, seen: list[str]) -> None:
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            break
        seen.append(line)
        if marker in line:
            return
    raise TimeoutError(f"{marker!r} not seen within {timeout}s; stderr so far:\n{''.join(seen)[-3000:]}")


def _send(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


@pytest.mark.skipif(sys.platform == "win32", reason="drives the follower over POSIX pipes")
def test_initialize_is_answered_while_the_election_stalls(tmp_path: Path) -> None:
    port = _free_port()
    proc = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", _SCRIPT, str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_isolated_env(tmp_path),
    )
    stdout: queue.Queue[str] = queue.Queue()
    stderr: queue.Queue[str] = queue.Queue()
    threading.Thread(target=_pump, args=(proc.stdout, stdout), daemon=True).start()
    threading.Thread(target=_pump, args=(proc.stderr, stderr), daemon=True).start()
    seen: list[str] = []
    try:
        # The election runs only once stdio is open, so seeing it start means
        # the client could already be talking to the follower.
        _wait_for_line(stderr, "ELECTION-STARTED", timeout=60.0, seen=seen)
        sent_at = time.monotonic()
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "e2e", "version": "0"},
                },
            },
        )
        init = _next_json(stdout, timeout=2.0, request_id="init-1")
        elapsed = time.monotonic() - sent_at
        assert init["result"]["serverInfo"]["name"] == "octowright"
        assert elapsed < 2.0
        while not stderr.empty():
            seen.append(stderr.get_nowait())
        assert not any("ELECTION-DONE" in line for line in seen), "answered only after the election"

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}})
        listed = _next_json(stdout, timeout=_ELECTION_STALL_SECONDS + 90.0, request_id="list-1")
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert "octowright_status" in names, listed
    finally:
        proc.kill()
        proc.wait(timeout=10)
