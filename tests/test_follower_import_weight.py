# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower (stdio<->leader-HTTP bridge) must stay lean.

Every connected MCP client spawns its own ``octowright serve`` follower process
that only bridges stdio to the leader's HTTP-MCP endpoint — it never drives a
browser. The entry point is ``octowright.cli:main``, so importing ``octowright.cli``
must NOT pull in Playwright, the browser pool, or the ~111-tool MCP registry
(~50MB the bridge never uses). With N connected clients that is N x ~50MB of pure
waste. This is a regression guard for that import-graph contract.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

# Imports the follower entry in a FRESH interpreter and reports any heavy module
# that got pulled in transitively (subprocess so other tests' imports can't leak in).
_PROBE = textwrap.dedent(
    """
    import sys
    import octowright.cli  # `octowright serve` -> octowright.cli:main
    heavy = sorted(
        m for m in sys.modules
        if m.startswith("playwright")
        or m.startswith("octowright.browser_pool")
        or m.startswith("octowright.server")
        or m == "starlette"
    )
    print("HEAVY:" + ",".join(heavy))
    """
)


def test_importing_cli_does_not_pull_the_browser_stack() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    line = next((ln for ln in result.stdout.splitlines() if ln.startswith("HEAVY:")), "HEAVY:<no output>")
    heavy = line.removeprefix("HEAVY:")
    assert heavy == "", (
        "importing octowright.cli (the follower entry) eagerly loaded heavy modules a "
        f"stdio bridge never needs: {heavy}"
    )


# Drives the real follower path (`_serve_async` with singleton coordination on)
# up to the leader election, with the election stubbed to report what is loaded
# at that moment and then hard-exit. Importing `octowright.cli` lean is not
# enough on its own: a function on the follower path can import the server
# lazily, and every client then pays for it before its stdio is even open.
# Starlette is not checked here: the MCP SDK's own HTTP client imports it, and
# the bridge needs that client.
_RUNTIME_PROBE = textwrap.dedent(
    """
    import asyncio
    import os
    import sys

    from octowright.cli import _leader_election as election
    from octowright.cli import serve

    async def report_and_exit(**_kwargs):
        heavy = sorted(
            m for m in sys.modules
            if m.startswith("playwright")
            or m.startswith("octowright.browser_pool")
            or m.startswith("octowright.server")
        )
        # stderr: by now stdout may be the MCP stdio transport.
        print("HEAVY:" + ",".join(heavy), file=sys.stderr, flush=True)
        os._exit(0)

    election.elect_leader = report_and_exit
    asyncio.run(
        serve._serve_async(
            http_host=None,
            http_port=None,
            no_http=False,
            keep_alive=True,
            idle_grace=None,
            no_singleton=False,
        )
    )
    """
)


def test_follower_path_reaches_the_election_without_the_browser_stack() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _RUNTIME_PROBE],
        capture_output=True,
        text=True,
        stdin=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    line = next((ln for ln in result.stderr.splitlines() if ln.startswith("HEAVY:")), "HEAVY:<no output>")
    heavy = line.removeprefix("HEAVY:")
    assert heavy == "", (
        "the follower path loaded heavy modules before electing a leader -- every "
        f"connected client pays for these: {heavy}\nstderr:\n{result.stderr[-2000:]}"
    )
