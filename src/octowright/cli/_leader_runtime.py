# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader run-phase plumbing extracted from ``cli.serve``.

Holds the two-phase leader lifecycle (`_run_leader_phases`) and the
"which task ended first" attribution logging (`_log_first_done`). Lives in its
own module so ``cli/serve.py`` stays under the per-file LOC gate; ``serve``
re-imports ``_run_leader_phases`` and calls it from ``_run_leader``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import click
from provide.telemetry import get_logger

_log = get_logger(__name__)


def _log_first_done(
    event: str,
    mcp_task: asyncio.Task[Any],
    watch_task: asyncio.Task[Any] | None,
    sidecars: list[asyncio.Task[Any]],
) -> None:
    """Log which task ended first so a daemon shutdown is attributable.

    Logged at INFO so it shows up in the default daemon log without needing
    --log-level=DEBUG. Includes the task that ended first plus a snapshot of
    the others' done/cancelled state so the user can tell whether shutdown
    came from the idle watchdog, a crashed sidecar, or stdio EOF.
    """
    finished: list[str] = []
    pending: list[str] = []
    for label, task in [("mcp", mcp_task), ("watchdog", watch_task)] + [
        (f"sidecar[{i}]", t) for i, t in enumerate(sidecars)
    ]:
        if task is None:
            continue
        if task.done():
            exc = task.exception() if not task.cancelled() else None
            tag = "cancelled" if task.cancelled() else ("error" if exc else "ok")
            finished.append(f"{label}={tag}")
        else:
            pending.append(label)
    _log.info(event, finished=finished, pending=pending)


async def _run_leader_phases(
    wait_for: set[Any],
    mcp_task: Any,
    watch_task: Any,
    sidecars: list[Any],
    discoverable: bool,
) -> None:
    """Two-phase leader life: wait for stdio-or-watchdog, then if only the
    stdio task ended on a discoverable leader, keep serving via HTTP-MCP
    until the watchdog or a sidecar fires."""
    await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)
    _log_first_done("octowright.leader.first_phase_ended", mcp_task, watch_task, sidecars)

    # If only the stdio MCP task ended (the typical "client disconnected"
    # case) and we're discoverable, keep serving via HTTP-MCP — waiting on the
    # HTTP sidecar (and the watchdog, when one is armed). This must NOT require a
    # watchdog: with auto-quit disabled (the default) the detached daemon would
    # otherwise exit the instant its /dev/null stdin EOFs, right after spawn.
    if mcp_task.done() and discoverable and (watch_task is None or not watch_task.done()):
        click.echo(
            "octowright: stdio client disconnected; leader staying alive for HTTP-MCP "
            "(reconnect by reopening your MCP client; auto-quit governed by --idle-grace)",
            err=True,
        )
        await asyncio.wait(set(filter(None, (watch_task, *sidecars))), return_when=asyncio.FIRST_COMPLETED)
        _log_first_done("octowright.leader.second_phase_ended", mcp_task, watch_task, sidecars)
