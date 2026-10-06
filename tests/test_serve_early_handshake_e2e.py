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

Runs on every platform, Windows included: the pipes are drained by reader
threads (``tests/_serve_subprocess.py``), never selected on, so the same test
covers the Proactor stdin reader thread and the cold-import time of the
follower before stdio opens on a Windows runner.

Hermetic: every state, config, lock and recordings path is under the test's
tmp dir (on the Windows lookups too), the HTTP port is a free one (never the
canonical 6286/6287), the boot and periodic orphan sweeps are off, and no
daemon is spawned.
"""

from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

from tests._serve_subprocess import (
    HANDSHAKE_BOUND_SECONDS,
    STARTUP_TIMEOUT_SECONDS,
    ServeProcess,
    free_port,
    isolated_env,
)

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


def test_initialize_is_answered_while_the_election_stalls(tmp_path: Path) -> None:
    serve = ServeProcess([sys.executable, "-c", _SCRIPT, str(free_port())], isolated_env(tmp_path))
    try:
        # The election runs only once stdio is open, so seeing it start means
        # the client could already be talking to the follower.
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
            f"initialize took {elapsed:.2f}s on {sys.platform}: the follower's stdin reader is not "
            "serving the handshake locally while the election runs"
        )
        assert not any("ELECTION-DONE" in line for line in serve.drain_stderr()), (
            f"initialize was answered only after the election finished on {sys.platform}: the "
            "follower did not open stdio before electing a leader"
        )

        serve.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        serve.send({"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}})
        listed = serve.answer("list-1", timeout=_ELECTION_STALL_SECONDS + 90.0)
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert "octowright_status" in names, (
            f"tools/list after the inline fallback did not come from the real registry on {sys.platform}: {listed!r}"
        )
    finally:
        serve.kill()
