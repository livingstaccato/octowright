# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A follower keeps its stdio while it spawns a real detached daemon (Windows).

Since stdio opens before the election, the follower's stdin is being read --
by anyio's reader thread, under the Proactor loop on Windows -- at the very
moment ``daemonize.spawn_daemon`` calls ``CreateProcess`` with the detach flags
from ``_detach_candidates``. Nothing else runs those two things together on
Windows, and both of the ways it could go wrong are silent:

* the follower stops answering on stdio during or after the spawn, which a
  client reports as a connect or request timeout; or
* the daemon inherits the follower's stdout/stderr pipe, so when the client
  goes away the pipe never reaches EOF and the client side hangs -- while the
  daemon, which should outlive the follower, is tied to its pipes.

Driven through the real CLI over real pipes, with the real election, the real
``_spawn_detached`` and the real flag ladder. Two things are replaced, both in
the follower script: ``wait_for_daemon`` sleeps a few seconds first, so a
``ping`` is guaranteed to land while the daemon process exists and the election
is unfinished; and the daemon entrypoint is a script that disables the
host-wide boot orphan sweep before running the real ``serve --daemon-mode``,
because a test must not reap browsers it does not own. The argv after the
entrypoint, the Popen call and its flags are unchanged.

Hermetic: isolated state/config/lock/log dirs (``tests/_serve_subprocess.py``)
and a free port, never 6286/6287. The daemon is killed in teardown whatever
happens.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess  # nosec B404
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

from tests._serve_subprocess import (
    HANDSHAKE_BOUND_SECONDS,
    STARTUP_TIMEOUT_SECONDS,
    ServeProcess,
    free_port,
    isolated_env,
    kill_process_tree,
    pid_alive,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the Windows detach flags (DETACHED_PROCESS / CREATE_BREAKAWAY_FROM_JOB) "
    "and handle inheritance alongside the Proactor stdin reader; POSIX is covered by setsid",
)

# Held open inside wait_for_daemon so the "during the spawn" ping cannot race
# the daemon's own startup.
_SPAWN_WINDOW_SECONDS = 3.0
_DAEMON_READY_SECONDS = 90.0
_FOLLOWER_EXIT_SECONDS = 30.0

_DAEMON_SCRIPT = textwrap.dedent(
    """
    import sys

    from octowright import housekeeping
    from octowright.cli import main

    housekeeping.reap_orphan_browsers_at_boot = lambda **_kwargs: None
    sys.argv = ["octowright", *sys.argv[1:]]
    main()
    """
)

_FOLLOWER_SCRIPT = textwrap.dedent(
    f"""
    import asyncio
    import sys

    from octowright import daemonize, housekeeping
    from octowright.cli import _leader_election as election
    from octowright.cli import main

    daemon_script, port = sys.argv[1], sys.argv[2]
    daemonize._resolve_daemon_entrypoint = lambda: [sys.executable, daemon_script]

    real_elect = election.elect_leader
    real_spawn = daemonize.spawn_daemon
    real_wait = daemonize.wait_for_daemon

    async def announced_election(**kwargs):
        print("ELECTION-STARTED", file=sys.stderr, flush=True)
        leader = await real_elect(**kwargs)
        print("ELECTION-DONE " + ("leader" if leader is not None else "none"), file=sys.stderr, flush=True)
        return leader

    def announced_spawn(**kwargs):
        pid = real_spawn(**kwargs)
        print(f"SPAWNED {{pid}}", file=sys.stderr, flush=True)
        return pid

    async def held_wait(*args, **kwargs):
        await asyncio.sleep({_SPAWN_WINDOW_SECONDS})
        return await real_wait(*args, **kwargs)

    election.elect_leader = announced_election
    daemonize.spawn_daemon = announced_spawn
    daemonize.wait_for_daemon = held_wait
    housekeeping.reap_orphan_browsers_at_boot = lambda **_kwargs: None
    sys.argv = ["octowright", "serve", "--http-port", port, "--keep-alive"]
    main()
    """
)


def _ping(serve: ServeProcess, request_id: str) -> float:
    sent = time.monotonic()
    serve.send({"jsonrpc": "2.0", "id": request_id, "method": "ping"})
    reply = serve.answer(request_id, timeout=HANDSHAKE_BOUND_SECONDS)
    assert "result" in reply, f"ping {request_id!r} was answered with an error: {reply!r}"
    return time.monotonic() - sent


def _health(port: int) -> dict[str, object] | None:
    url = f"http://127.0.0.1:{port}/api/health"
    with contextlib.suppress(OSError, ValueError), urllib.request.urlopen(url, timeout=5) as response:  # nosec B310
        body = json.loads(response.read().decode("utf-8"))
        return body if isinstance(body, dict) else None
    return None


def test_follower_stdio_survives_a_real_detached_daemon_spawn(
    tmp_path: Path, record_property: pytest.RecordProperty
) -> None:
    port = free_port()
    daemon_script = tmp_path / "daemon_entry.py"
    daemon_script.write_text(_DAEMON_SCRIPT, encoding="utf-8")
    follower_script = tmp_path / "follower_entry.py"
    follower_script.write_text(_FOLLOWER_SCRIPT, encoding="utf-8")
    env = isolated_env(tmp_path)
    env["OCTOWRIGHT_DAEMON_READY_TIMEOUT"] = str(_DAEMON_READY_SECONDS)

    serve = ServeProcess([sys.executable, str(follower_script), str(daemon_script), str(port)], env)
    daemon_pid: int | None = None
    try:
        serve.wait_for_stderr("ELECTION-STARTED", timeout=STARTUP_TIMEOUT_SECONDS)
        serve.send(
            {
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "win-spawn", "version": "0"},
                },
            }
        )
        init = serve.answer("init-1", timeout=HANDSHAKE_BOUND_SECONDS)
        assert init["result"]["serverInfo"]["name"] == "octowright"

        spawned = serve.wait_for_stderr("SPAWNED ", timeout=STARTUP_TIMEOUT_SECONDS)
        daemon_pid = int(spawned.split()[-1])

        # The daemon process exists and the election is still in its readiness
        # wait: this is the window H8 names.
        during = _ping(serve, "ping-during")
        assert not any(line.startswith("ELECTION-DONE") for line in serve.drain_stderr()), (
            "the 'during the spawn' ping landed after the election finished, so it proved nothing; "
            "the held readiness wait did not hold"
        )
        assert during < HANDSHAKE_BOUND_SECONDS, (
            f"ping took {during:.2f}s while CreateProcess had just spawned the detached daemon: the "
            "follower's stdin reader stalled across the spawn"
        )

        done = serve.wait_for_stderr("ELECTION-DONE", timeout=_DAEMON_READY_SECONDS + 30.0)
        assert done.strip() == "ELECTION-DONE leader", (
            f"the detached daemon (pid {daemon_pid}) never answered, so the follower fell back to an "
            f"inline leader; the daemon was spawned but did not start under these flags. {serve.stderr_excerpt()}"
        )
        stderr_text = "".join(serve.drain_stderr())
        assert "daemon.spawn_detached" in stderr_text, (
            "no daemon.spawn_detached record on the follower's stderr, so which detach candidate won "
            f"is unknown. {serve.stderr_excerpt()}"
        )
        refused = "daemon.detach_candidate_refused" in stderr_text
        attempt = re.search(r"daemon\.spawn_detached.*?attempt\W+(\d+)", stderr_text)
        record_property("detach_breakaway_refused", refused)
        record_property("detach_attempt", attempt.group(1) if attempt else "unparsed")

        serve.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        serve.send({"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}})
        listed = serve.answer("list-1", timeout=60.0)
        assert "result" in listed, (
            f"tools/list through the bridge to the daemon was refused: {listed!r}. {serve.stderr_excerpt()}"
        )
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert "octowright_status" in names, f"tools/list through the bridge to the daemon failed: {listed!r}"
        after = _ping(serve, "ping-after")
        assert after < HANDSHAKE_BOUND_SECONDS, f"ping after the daemon came up took {after:.2f}s"

        # The client goes away. The follower must exit, and both of its output
        # pipes must reach EOF: a daemon holding either write end keeps it open.
        serve.close_stdin()
        try:
            serve.proc.wait(timeout=_FOLLOWER_EXIT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(
                f"the follower did not exit within {_FOLLOWER_EXIT_SECONDS}s of its stdin closing "
                f"after spawning the daemon. {serve.stderr_excerpt()}"
            ) from exc
        assert serve.stdout_eof.wait(timeout=10.0), (
            f"the follower exited but its stdout pipe never reached EOF: the detached daemon "
            f"(pid {daemon_pid}) inherited the follower's stdout handle"
        )
        assert serve.stderr_eof.wait(timeout=10.0), (
            f"the follower exited but its stderr pipe never reached EOF: the detached daemon "
            f"(pid {daemon_pid}) inherited the follower's stderr handle"
        )

        assert pid_alive(daemon_pid), (
            f"the detached daemon (pid {daemon_pid}) died with the follower it was spawned from"
        )
        health = _health(port)
        assert health is not None and health.get("ok") is True, (
            f"the daemon (pid {daemon_pid}) stopped answering /api/health on port {port} once its "
            f"follower exited: {health!r}"
        )
    finally:
        serve.kill()
        if daemon_pid is not None:
            kill_process_tree(daemon_pid)
