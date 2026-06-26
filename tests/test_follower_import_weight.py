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
