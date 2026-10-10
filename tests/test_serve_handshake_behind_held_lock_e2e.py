# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""End to end: a raised ``--ready-timeout`` behind a held election lock does
not delay the client's ``initialize``.

A starter waits for the election lock for ``--ready-timeout`` plus headroom,
and then for the holder's leader for ``--ready-timeout`` again, before it falls
back inline -- 30s at the default 10s, past a minute at 25s. MCP clients bound
the handshake far tighter (Claude Code's is 30s), so if ``initialize`` waited
on the election, raising the budget for a slow host would make every client
behind a long lock holder (an ``octowright restart``, a cold spawn) fail to
connect at all.

It does not wait: the follower opens stdio and answers ``initialize`` locally
(``mcp_identity``) while the election runs, and only the first tool call waits.
``test_serve_early_handshake_e2e.py`` pins that with the election replaced by a
stub; this drives the REAL election into a lock the test holds, with a minute's
readiness budget, so the bound is shown against the actual wait it is about.

Hermetic: every state, config, lock and recordings path is under the test's
tmp dir, the HTTP port is a free one, the test holds the lock for the whole run
so nothing is spawned, and the process is killed before it could fall back.
"""

from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

from octowright import singleton
from tests._serve_subprocess import (
    HANDSHAKE_BOUND_SECONDS,
    STARTUP_TIMEOUT_SECONDS,
    ServeProcess,
    free_port,
    isolated_env,
)

# Far past any MCP client's handshake timeout once the lock wait (this plus
# headroom) and the contended readiness wait (this again) are added up.
_READY_TIMEOUT_SECONDS = 60

_SCRIPT = textwrap.dedent(
    """
    import sys

    from octowright import housekeeping
    from octowright.cli import _leader_election as election
    from octowright.cli import main

    real_elect = election.elect_leader

    async def announced_election(**kwargs):
        print("ELECTION-STARTED", file=sys.stderr, flush=True)
        return await real_elect(**kwargs)

    election.elect_leader = announced_election
    housekeeping.reap_orphan_browsers_at_boot = lambda **_kwargs: None
    sys.argv = [
        "octowright", "serve", "--http-port", sys.argv[1], "--keep-alive", "--ready-timeout", sys.argv[2],
    ]
    main()
    """
)


def test_initialize_is_answered_behind_a_held_lock_with_a_long_ready_timeout(tmp_path: Path) -> None:
    env = isolated_env(tmp_path)
    lock_path = Path(env["OCTOWRIGHT_LOCK_PATH"])
    with singleton.election_lock(lock_path, timeout=5.0):
        serve = ServeProcess(
            [sys.executable, "-c", _SCRIPT, str(free_port()), str(_READY_TIMEOUT_SECONDS)],
            env,
        )
        try:
            serve.wait_for_stderr("ELECTION-STARTED", timeout=STARTUP_TIMEOUT_SECONDS)
            sent_at = time.monotonic()
            serve.send(
                {
                    "jsonrpc": "2.0",
                    "id": "init-1",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "e2e", "version": "0"},
                    },
                }
            )
            init = serve.answer("init-1", timeout=HANDSHAKE_BOUND_SECONDS)
            elapsed = time.monotonic() - sent_at
            assert init["result"]["serverInfo"]["name"] == "octowright"
            assert elapsed < HANDSHAKE_BOUND_SECONDS, (
                f"initialize took {elapsed:.2f}s behind a held election lock with --ready-timeout "
                f"{_READY_TIMEOUT_SECONDS} on {sys.platform}: the handshake is waiting on the election"
            )
            serve.send({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"})
            assert "result" in serve.answer("ping-1", timeout=HANDSHAKE_BOUND_SECONDS)
            # Still electing: the lock is held, so nothing may have been spawned
            # or served inline yet -- the answers above were the follower's own.
            stderr = "".join(serve.drain_stderr())
            assert "spawning daemon" not in stderr, stderr
            assert serve.proc.poll() is None, serve.stderr_excerpt()
        finally:
            serve.kill()
