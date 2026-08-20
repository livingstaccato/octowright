# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright serve --wait-ready`` -- the CI/scripting readiness entry point.

The election itself lives in :mod:`cli._leader_election`, shared with every
other caller. This module is only the command body and the stderr reporting
around it.
"""

from __future__ import annotations

import click

from octowright.cli import _leader_election as _election


def echo_daemon_log_tail() -> None:
    """Quote the detached daemon's stderr so a spawn failure states a reason.

    The daemon's output goes to its own 0600 log, so the caller's stderr holds
    the *follower's* output and is empty on exactly this failure -- the file
    the error message points you at is the wrong one.
    """
    from octowright import daemonize as _daemon

    click.echo(f"octowright: last lines of {_daemon.daemon_log_path()}:", err=True)
    click.echo(_daemon.daemon_log_tail(), err=True)


async def wait_ready(
    *,
    http_host: str | None,
    http_port: int | None,
    idle_grace: float | None,
    keep_alive: bool,
) -> None:
    """Ensure a daemon leader exists and is answering, then exit.

    Every workflow that wanted "start it and tell me when it's ready" had to
    background ``serve`` and hand-roll a lockfile poll in bash/pwsh, then guess
    at the reason when the file never appeared. The readiness probe here is the
    same ``wait_for_daemon`` the follower already uses, and a failure quotes the
    daemon log -- the only place the reason was ever written.

    Deliberately has no inline-leader fallback: that path keeps serving in the
    foreground forever, which is right for an MCP client that wants *a* working
    server and wrong for a script whose contract is an exit code.
    """
    from octowright import daemonize as _daemon

    try:
        leader = await _election.elect_leader(
            http_host=http_host, http_port=http_port, idle_grace=idle_grace, keep_alive=keep_alive
        )
    except _election.ElectionContended:
        # Another instance was electing and never produced a leader. Do NOT
        # quote our daemon log: we spawned nothing, so it describes a different
        # process (or is empty) and would send the reader after the wrong one.
        click.echo(
            f"octowright: another instance is electing a leader and it did not become ready "
            f"within {_daemon.daemon_ready_timeout():g}s; check that instance, "
            f"or raise --ready-timeout",
            err=True,
        )
        raise SystemExit(1) from None
    if leader is None:
        click.echo(
            f"octowright: daemon did not become ready within {_daemon.daemon_ready_timeout():g}s "
            f"(raise it with --ready-timeout or {_daemon.DAEMON_READY_TIMEOUT_ENV})",
            err=True,
        )
        echo_daemon_log_tail()
        raise SystemExit(1)
    click.echo(f"octowright: leader ready at {leader.mcp_url}", err=True)
    click.echo(leader.mcp_url)
