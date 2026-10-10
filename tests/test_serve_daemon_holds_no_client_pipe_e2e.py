# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The detached daemon holds none of the MCP client's pipes, on every platform.

A follower opens its stdio -- the client's pipes -- before it elects a leader,
so the election spawns the detached daemon while those pipes are open (on
Windows with anyio's stdin reader thread running under the Proactor loop). If
the daemon inherited any of them, the failure is silent and lands on the
client, not on Octowright:

* the follower's **stdout** (or stderr): when the client goes away and the
  follower exits, the client never sees EOF, because the daemon -- which
  outlives the follower by design -- still holds the write end. The client
  hangs waiting for a server that is gone.
* the follower's **stdin**: the daemon holds a read end of the client's
  requests, so it can consume bytes meant for the follower, and the client's
  writes never fail once the follower is dead.

Two layers keep the pipes out of the daemon, and they cover different spawns:

* ``daemonize._spawn_detached`` gives the daemon DEVNULL for stdin and stdout,
  the daemon log for stderr and ``close_fds=True`` (on Windows that restricts
  inheritance to exactly those three handles).
* While the bridge serves, mcp's ``stdio_server`` moves the wire to private
  close-on-exec descriptors and points fd 0 at the null device and fd 1 at
  stderr (rebinding the Win32 std handles too). That covers the election's
  spawn -- but the bridge restores fd 0/1 to the wire when it returns, so the
  *respawn* after a lost leader (``_respawn_if_leader_gone``) runs with the
  client's real stdin and stdout on fd 0/1, and only the first layer stands
  between them and the replacement daemon.

So the client side is exercised three ways, through the real CLI over real
pipes, the real election and the real ``_spawn_detached``: the client closes
stdin; the client kills the server; and the leader dies, so the follower
spawns a replacement from the respawn path and exits. Each time every pipe the
client holds must report that its far side is gone -- stdout and stderr reach
EOF, and a write to stdin fails -- while the daemon is still serving.

Only the daemon entrypoint is replaced (``DAEMON_ENTRY_SCRIPT``: the real
``serve --daemon-mode`` without the host-wide boot orphan sweep); the argv
after it, the Popen call, its stdio and its detach flags are unchanged. The
follower announces each pid it spawned and the election's result on stderr.

Portable: pipes are drained by reader threads (``tests/_serve_subprocess.py``),
never selected on or set non-blocking, and daemons are stopped by pid with
``taskkill /T`` on Windows and their session group on POSIX. Hermetic: isolated
state/config/lock/log/temp dirs and a free port, never 6286/6287. No browser
is launched.
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
import textwrap
from pathlib import Path

import pytest

from octowright import singleton
from tests._serve_subprocess import (
    DAEMON_ENTRY_SCRIPT,
    STARTUP_TIMEOUT_SECONDS,
    ServeProcess,
    free_port,
    health,
    isolated_env,
    kill_process_tree,
    pid_alive,
)

_DAEMON_READY_SECONDS = 90.0
_FOLLOWER_EXIT_SECONDS = 30.0
# How long the follower keeps retrying a dead leader before it gives up and
# respawns one (default 180s); short, because waiting it out is the scenario.
_LEADER_RECOVERY_WINDOW_SECONDS = 3.0
# Once the follower has exited and been reaped, the OS closes its ends of the
# pipes at once; this only bounds how long the reader thread takes to be
# scheduled. Waited out in full only when the assertion fails.
_EOF_SECONDS = 10.0

_FOLLOWER_SCRIPT = textwrap.dedent(
    """
    import sys

    from octowright import daemonize, housekeeping
    from octowright.cli import _leader_election as election
    from octowright.cli import main

    daemon_script, port = sys.argv[1], sys.argv[2]
    daemonize._resolve_daemon_entrypoint = lambda: [sys.executable, daemon_script]

    real_elect = election.elect_leader
    real_spawn = daemonize.spawn_daemon

    async def announced_election(**kwargs):
        leader = await real_elect(**kwargs)
        print("ELECTION-DONE " + ("leader" if leader is not None else "none"), file=sys.stderr, flush=True)
        return leader

    def announced_spawn(**kwargs):
        pid = real_spawn(**kwargs)
        print(f"SPAWNED {pid}", file=sys.stderr, flush=True)
        return pid

    election.elect_leader = announced_election
    daemonize.spawn_daemon = announced_spawn
    housekeeping.reap_orphan_browsers_at_boot = lambda **_kwargs: None
    sys.argv = ["octowright", "serve", "--http-port", port, "--keep-alive"]
    main()
    """
)

_SCENARIOS = ["client_closes_stdin", "client_kills_the_server", "leader_dies_and_is_respawned"]


def _bring_up_follower_with_daemon(serve: ServeProcess) -> int:
    """Handshake, wait for the follower to spawn a daemon and bridge to it.

    Returns the pid the follower's ``Popen`` got. ``tools/list`` is answered
    only through the bridge to the daemon, so after it the follower holds a
    live session with the daemon -- the state a real client leaves from.
    """
    serve.send(
        {
            "jsonrpc": "2.0",
            "id": "init-1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pipe-inheritance", "version": "0"},
            },
        }
    )
    init = serve.answer("init-1", timeout=STARTUP_TIMEOUT_SECONDS)
    assert init["result"]["serverInfo"]["name"] == "octowright", init

    spawned = serve.wait_for_stderr("SPAWNED ", timeout=STARTUP_TIMEOUT_SECONDS)
    spawned_pid = int(spawned.split()[-1])
    done = serve.wait_for_stderr("ELECTION-DONE", timeout=_DAEMON_READY_SECONDS + 30.0)
    assert done.strip() == "ELECTION-DONE leader", (
        f"the detached daemon (pid {spawned_pid}) never answered, so the follower fell back to an "
        f"inline leader and nothing here is about a detached daemon. {serve.stderr_excerpt()}"
    )

    serve.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    serve.send({"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}})
    listed = serve.answer("list-1", timeout=60.0)
    assert "result" in listed, f"tools/list through the bridge to the daemon failed: {listed!r}"
    return spawned_pid


def _with_lock_pid(lock_path: Path, pids: list[int]) -> list[int]:
    """*pids* plus the pid the lockfile names, if it is not already among them.

    On Windows the pid ``Popen`` returned can be a venv launcher whose child is
    the real daemon; the lockfile names the process that serves. ``read_lock``
    never raises: a missing or corrupt lock is None.
    """
    info = singleton.read_lock(lock_path)
    if info is not None and info.pid not in pids:
        return [*pids, info.pid]
    return list(pids)


def _assert_follower_gone_and_pipes_released(serve: ServeProcess, how: str, holders: str, *, within: float) -> None:
    try:
        serve.proc.wait(timeout=within)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"the follower did not exit within {within}s {how} on {sys.platform}. {serve.stderr_excerpt()}"
        ) from exc
    assert serve.stdout_eof.wait(timeout=_EOF_SECONDS), (
        f"the follower exited (rc={serve.proc.returncode}) {how}, but its stdout never reached EOF "
        f"on {sys.platform}: another process holds the write end of the client's pipe -- the "
        f"detached daemon ({holders}) inherited it, so a real client would hang here"
    )
    assert serve.stderr_eof.wait(timeout=_EOF_SECONDS), (
        f"the follower exited {how}, but its stderr never reached EOF on {sys.platform}: the "
        f"detached daemon ({holders}) inherited the follower's stderr"
    )


def _assert_nobody_reads_stdin(serve: ServeProcess, holders: str) -> None:
    """The client still holds the write end of the dead follower's stdin.

    A write must fail; it succeeds only if another process still holds a read
    end of the client's request pipe.
    """
    assert serve.proc.stdin is not None
    try:
        serve.proc.stdin.write('{"jsonrpc": "2.0", "method": "ping", "id": "after-exit"}\n')
        serve.proc.stdin.flush()
    except OSError:
        pass  # EPIPE on POSIX; EPIPE or EINVAL (ERROR_NO_DATA) on Windows: nobody reads
    else:
        raise AssertionError(
            f"a write to the exited follower's stdin succeeded on {sys.platform}: the detached "
            f"daemon ({holders}) holds a read end of the client's request pipe"
        )
    finally:
        serve.close_stdin()


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_the_detached_daemon_holds_none_of_the_clients_pipes(tmp_path: Path, scenario: str) -> None:
    port = free_port()
    daemon_script = tmp_path / "daemon_entry.py"
    daemon_script.write_text(DAEMON_ENTRY_SCRIPT, encoding="utf-8")
    follower_script = tmp_path / "follower_entry.py"
    follower_script.write_text(_FOLLOWER_SCRIPT, encoding="utf-8")
    env = isolated_env(tmp_path)
    env["OCTOWRIGHT_DAEMON_READY_TIMEOUT"] = str(_DAEMON_READY_SECONDS)
    env["OCTOWRIGHT_BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS"] = str(_LEADER_RECOVERY_WINDOW_SECONDS)
    lock_path = Path(env["OCTOWRIGHT_LOCK_PATH"])

    serve = ServeProcess([sys.executable, str(follower_script), str(daemon_script), str(port)], env)
    # Every daemon pid seen, so teardown stops all of them whatever fails.
    spawned: list[int] = []
    try:
        spawned.append(_bring_up_follower_with_daemon(serve))
        assert singleton.read_lock(lock_path) is not None, (
            f"no lockfile at {lock_path} although the election returned a leader"
        )
        spawned = live = _with_lock_pid(lock_path, spawned)

        if scenario == "client_closes_stdin":
            serve.close_stdin()
            holders = "pid " + " / ".join(map(str, live))
            _assert_follower_gone_and_pipes_released(
                serve, "after the client closed stdin", holders, within=_FOLLOWER_EXIT_SECONDS
            )
        elif scenario == "client_kills_the_server":
            # TerminateProcess / SIGKILL on the follower alone: ``serve.kill``
            # would take the tree, daemon included, on Windows.
            serve.proc.kill()
            holders = "pid " + " / ".join(map(str, live))
            _assert_follower_gone_and_pipes_released(
                serve, "after the client killed it", holders, within=_FOLLOWER_EXIT_SECONDS
            )
            _assert_nobody_reads_stdin(serve, holders)
        else:
            for pid in live:
                kill_process_tree(pid)
            # The follower retries the dead leader for the recovery window and
            # then cancels the bridge. The bridge returns -- restoring fd 0/1 to
            # the client's pipes -- only once its stdin reader thread does,
            # which a thread blocked in readline cannot do until the client
            # writes (or closes stdin). A client's next call is that write; the
            # stdin probe below needs stdin open, so send one.
            serve.wait_for_stderr(
                "leader_recovery_window_exhausted",
                timeout=_LEADER_RECOVERY_WINDOW_SECONDS + STARTUP_TIMEOUT_SECONDS,
            )
            serve.send({"jsonrpc": "2.0", "id": "ping-after-leader-death", "method": "ping"})
            # Then the respawn path spawns a replacement daemon, waits for it,
            # and the follower exits.
            respawned = serve.wait_for_stderr(
                "SPAWNED ",
                occurrence=2,
                timeout=_LEADER_RECOVERY_WINDOW_SECONDS + STARTUP_TIMEOUT_SECONDS,
            )
            replacement = int(respawned.split()[-1])
            spawned.append(replacement)
            holders = f"replacement pid {replacement}"
            _assert_follower_gone_and_pipes_released(
                serve,
                "after its leader died and it spawned a replacement",
                holders,
                within=_DAEMON_READY_SECONDS + _FOLLOWER_EXIT_SECONDS,
            )
            live = _with_lock_pid(lock_path, [replacement])
            spawned = _with_lock_pid(lock_path, spawned)
            _assert_nobody_reads_stdin(serve, holders)

        for pid in live:
            assert pid_alive(pid), f"the detached daemon (pid {pid}) died with the follower it was spawned from"
        answer = health(port)
        assert answer is not None and answer.get("ok") is True, (
            f"the daemon (pid {' / '.join(map(str, live))}) is not answering /api/health on port {port} "
            f"once its follower was gone: {answer!r}"
        )
    finally:
        serve.kill()
        # Read again in case an assertion failed before the lock was read.
        for pid in _with_lock_pid(lock_path, spawned):
            kill_process_tree(pid)
