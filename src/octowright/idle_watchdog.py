# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Idle watchdog — exits the server after the pool sits empty for a while.

Used by ``octowright serve`` so the long-running stdio process doesn't linger
forever after the user has closed every browser. The watchdog only arms once
at least one session (browser or scenario) has existed; a freshly-started
server with no clients yet will never exit on its own.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pool import BrowserPool
    from .scenarios import ScenarioPool


async def idle_watchdog(
    pool: BrowserPool,
    scenario_pool: ScenarioPool,
    *,
    grace_seconds: float = 30.0,
    poll_seconds: float = 2.0,
    arm_immediately: bool = False,
    get_extra_active_count: Callable[[], int] | None = None,
) -> None:
    """Return once the pool has been used and then sat empty for ``grace_seconds``.

    With ``arm_immediately=False`` (default, suitable for direct-CLI usage),
    the watchdog only arms after the pool first becomes non-empty — so a
    user starting the server in a terminal has time to issue their first
    launch command without being kicked out.

    With ``arm_immediately=True`` (used by daemonized leaders), the timer
    starts at t=0. An idle daemon nobody connects to exits after the grace
    period instead of running forever.

    ``get_extra_active_count`` is an optional callable returning a count of
    additional "active" connections that should prevent shutdown — used by
    daemon leaders to include active HTTP-MCP proxy sessions in the liveness
    check so the daemon doesn't exit while a follower is connected.

    The caller is expected to trigger shutdown when this coroutine returns.
    """
    armed = arm_immediately
    idle_since: float | None = None
    while True:
        await asyncio.sleep(poll_seconds)
        extra = get_extra_active_count() if get_extra_active_count is not None else 0
        active = bool(pool.list_sessions()) or bool(scenario_pool.list_live()) or extra > 0
        if active:
            armed = True
            idle_since = None
            continue
        if not armed:
            continue
        now = time.monotonic()
        if idle_since is None:
            idle_since = now
            continue
        if now - idle_since >= grace_seconds:
            return
