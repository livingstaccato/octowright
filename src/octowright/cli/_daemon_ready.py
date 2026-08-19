# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The one split-brain-guarded election, and the CI-facing readiness command.

Lives beside :mod:`cli._leader_election` (and for the same reason) to keep
``cli.serve`` under its LOC ceiling. More importantly it keeps the election
dance in ONE place: probe -> lock -> re-probe -> adopt the canonical port ->
spawn -> confirm under the lock. That sequence is the repo's most-patched
invariant, and a second copy written for ``--wait-ready`` would drift out of
step with it -- leaving one caller spawning a competitor on a bumped port
after a fix landed on the other.

The two callers differ only in what to do when the daemon never answers:
``serve`` runs the leader inline so the user still gets *a* server, while
``--wait-ready`` exits non-zero because a script's whole contract is an exit
code. That difference is the return value, not a second election.
"""

from __future__ import annotations

from typing import Any

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


async def elect_leader(
    *,
    http_host: str | None,
    http_port: int | None,
    idle_grace: float | None,
    keep_alive: bool,
) -> Any:
    """Find, adopt, or spawn a daemon leader. ``None`` if it never answered.

    Raises ``TimeoutError`` when another instance holds the election lock;
    callers decide whether that means "defer" or "not ready".
    """
    from octowright import daemonize as _daemon
    from octowright import singleton as _sn

    # Probe outside the lock; recheck under it to avoid a duplicate spawn.
    if (found := await _election._probe_alive_leader(_sn)) is not None:
        return found
    async with _sn.async_election_lock(timeout=_election_lock_timeout()):
        if (found := await _election._probe_alive_leader(_sn)) is not None:
            return found
        # Split-brain guard: the lockfile says no leader, but a healthy
        # octowright may already hold the canonical port (lockfile lag / stale
        # lock). Adopt it rather than spawn a competitor on a bumped port.
        if (found := await _election._adopt_canonical_leader(_sn, http_host, http_port)) is not None:
            click.echo("octowright: adopted existing leader on canonical port; not spawning", err=True)
            return found
        click.echo("octowright: no live leader; spawning daemon", err=True)
        _daemon.spawn_daemon(http_host=http_host, http_port=http_port, idle_grace=idle_grace, keep_alive=keep_alive)
        # Confirm the daemon is up while still holding the election lock, so a
        # concurrent starter blocks until the leader exists and then adopts it
        # instead of spawning a competitor on a bumped port (split-brain).
        return await _daemon.wait_for_daemon()


def _election_lock_timeout() -> float:
    """How long a concurrent starter waits for the election lock.

    Must exceed the readiness budget: the holder keeps the lock across
    ``wait_for_daemon``, so a raised ``--ready-timeout`` (the documented fix
    for a cold container) would otherwise make every *other* ``serve`` give up
    first and take a path that treats contention as an error. Scaled with
    headroom for the spawn itself.
    """
    from octowright import daemonize as _daemon

    return _daemon.daemon_ready_timeout() + _ELECTION_LOCK_HEADROOM_SECONDS


_ELECTION_LOCK_HEADROOM_SECONDS = 10.0


def report_not_ready(reason: str) -> None:
    """Explain a readiness failure on stderr, with the daemon log tail."""
    click.echo(f"octowright: {reason}", err=True)
    echo_daemon_log_tail()


async def wait_ready(
    *,
    http_host: str | None,
    http_port: int | None,
    idle_grace: float | None,
    keep_alive: bool,
) -> None:
    """Ensure a daemon leader exists and is answering, then exit.

    The scripting/CI counterpart to the stdio bridge: every workflow that
    wanted "start it and tell me when it's ready" had to background ``serve``
    and hand-roll a lockfile poll in bash/pwsh, then guess at the reason when
    the file never appeared. Here the readiness probe is the same
    ``wait_for_daemon`` the follower already uses, and a failure quotes the
    daemon log -- the only place the reason was ever written.

    Deliberately has no inline-leader fallback: that path keeps serving in the
    foreground forever, which is right for an MCP client that wants *a*
    working server and wrong for a script whose contract is an exit code.

    Prints the leader's MCP URL on stdout so a workflow can capture it, and
    human-readable status on stderr. Raises ``SystemExit(1)`` when no leader
    is reachable within the budget.
    """
    from octowright import daemonize as _daemon

    try:
        leader = await elect_leader(
            http_host=http_host, http_port=http_port, idle_grace=idle_grace, keep_alive=keep_alive
        )
    except TimeoutError:
        click.echo("octowright: another instance is electing a leader; not ready", err=True)
        raise SystemExit(1) from None
    if leader is None:
        report_not_ready(
            f"daemon did not become ready within {_daemon.daemon_ready_timeout():g}s "
            f"(raise it with --ready-timeout or {_daemon.DAEMON_READY_TIMEOUT_ENV})"
        )
        raise SystemExit(1)
    click.echo(f"octowright: leader ready at {leader.mcp_url}", err=True)
    click.echo(leader.mcp_url)
