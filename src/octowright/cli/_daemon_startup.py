# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Startup guard for a ``serve --daemon-mode`` leader that no election started.

Every spawner inside Octowright (the startup election, the post-bridge respawn,
``octowright restart``) holds the election lock across spawn and readiness, so
a peer that races it waits, sees the new leader, and defers. A daemon a service
manager starts -- a systemd user unit whose ``ExecStart`` runs
``serve --daemon-mode`` -- took no lock, and that left the window open.

Observed live (2026-11-03): ``systemctl --user restart`` started the new daemon,
whose cold imports took 7s before it bound. Three seconds in, a new MCP client's
follower ran the election, found no lockfile and a free port, and spawned its
own daemon. Both bound four seconds later; the unit got 6286 and the follower's
daemon port-walked to 6287 -- two leaders, and the second one held browsers for
an hour before anyone noticed.

So a daemon-mode leader that was NOT spawned under the lock takes it itself,
before its slow imports, and holds it until its lockfile is written. Under the
lock it first checks for a live leader (the lockfile's, verified over HTTP, or
an Octowright already on the requested port) and, if there is one, does not
start at all: starting would only port-walk beside it. A spawner that already
holds the lock says so with ``SPAWNED_UNDER_LOCK_ENV``; the child must not wait
for the lock its own parent is holding (``restart`` once stalled exactly so).
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from octowright.singleton import HeldElectionLock

SPAWNED_UNDER_LOCK_ENV = "OCTOWRIGHT_DAEMON_SPAWNED_UNDER_LOCK"


async def claim_daemon_start(*, http_host: str | None, http_port: int | None) -> HeldElectionLock | None:
    """Prepare a daemon-mode start.

    Returns a lock holder whose ``release()`` the caller invokes once the
    lockfile is written (a no-op holder when the spawner holds the lock), or
    ``None`` when a live leader already serves and this daemon must not start.
    """
    from octowright import singleton as _sn
    from octowright.cli import _leader_election as _election

    if os.environ.pop(SPAWNED_UNDER_LOCK_ENV, "") == "1":
        return _sn.HeldElectionLock(None)
    budget = _election._election_lock_timeout()
    try:
        hold = await _sn.HeldElectionLock.acquire(timeout=budget)
    except TimeoutError:
        # A peer has held it for the whole budget. Decide on the probes alone
        # rather than wait forever; the probes still catch a leader it started.
        click.echo("octowright: election lock busy; checking for a live leader without it", err=True)
        hold = _sn.HeldElectionLock(None)
    found = await _election._probe_alive_leader(_sn)
    if found is not None or await _election._canonical_port_serves_octowright(http_host, http_port):
        hold.release()
        where = found.mcp_url if found is not None else "the requested HTTP port"
        click.echo(
            f"octowright: a leader is already serving at {where}; not starting a second one "
            "(it would bind a bumped port beside it)",
            err=True,
        )
        return None
    if hold.held:
        # Never hold it for longer than a peer would wait: a leader whose HTTP
        # never binds must not wedge every later election.
        asyncio.get_running_loop().call_later(budget, hold.release)
    return hold
