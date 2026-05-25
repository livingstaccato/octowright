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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from provide.telemetry import get_logger

if TYPE_CHECKING:
    from octowright.browser_pool import BrowserPool
    from octowright.scenarios_pool import ScenarioPool

log = get_logger(__name__)


@dataclass
class _WatchdogState:
    """Mutable state owned by the watchdog loop. armed=True after the pool
    has been used at least once (or arm_immediately=True at start);
    idle_since is set the first tick the pool goes idle after arming."""

    armed: bool = False
    idle_since: float | None = None

    def idle_for_seconds(self) -> float | None:
        if self.idle_since is None:
            return None
        return time.monotonic() - self.idle_since


@dataclass
class _PoolSnapshot:
    """Snapshot of the pool at one tick."""

    browsers: int
    scenarios: int
    extra: int

    @property
    def active(self) -> bool:
        return self.browsers > 0 or self.scenarios > 0 or self.extra > 0


def _sample_pool(
    pool: BrowserPool,
    scenario_pool: ScenarioPool,
    get_extra_active_count: Callable[[], int] | None,
) -> _PoolSnapshot:
    return _PoolSnapshot(
        browsers=len(pool.list_sessions()),
        scenarios=len(scenario_pool.list_live()),
        extra=get_extra_active_count() if get_extra_active_count is not None else 0,
    )


def _on_active_tick(state: _WatchdogState, snapshot: _PoolSnapshot) -> None:
    """An active tick clears any pending idle countdown and arms the watchdog."""
    if not state.armed:
        log.info(
            "octowright.watchdog.armed",
            browsers=snapshot.browsers,
            scenarios=snapshot.scenarios,
            extra=snapshot.extra,
        )
    state.armed = True
    if state.idle_since is not None:
        log.info(
            "octowright.watchdog.idle_cleared",
            browsers=snapshot.browsers,
            scenarios=snapshot.scenarios,
            extra=snapshot.extra,
        )
    state.idle_since = None


def _check_idle_expiry(
    state: _WatchdogState,
    snapshot: _PoolSnapshot,
    grace_seconds: float,
) -> bool:
    """An idle tick: start the countdown if needed, fire if grace exceeded.
    Returns True iff the watchdog should exit (caller should return)."""
    now = time.monotonic()
    if state.idle_since is None:
        log.info("octowright.watchdog.idle_started", grace_seconds=grace_seconds)
        state.idle_since = now
        return False
    elapsed = now - state.idle_since
    if elapsed < grace_seconds:
        return False
    log.info(
        "octowright.watchdog.fired",
        idle_seconds=elapsed,
        grace_seconds=grace_seconds,
        browsers=snapshot.browsers,
        scenarios=snapshot.scenarios,
        extra=snapshot.extra,
    )
    return True


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
    state = _WatchdogState(armed=arm_immediately)
    log.info(
        "octowright.watchdog.start",
        grace_seconds=grace_seconds,
        poll_seconds=poll_seconds,
        armed=state.armed,
    )
    while True:
        await asyncio.sleep(poll_seconds)
        snapshot = _sample_pool(pool, scenario_pool, get_extra_active_count)
        log.debug(
            "octowright.watchdog.tick",
            browsers=snapshot.browsers,
            scenarios=snapshot.scenarios,
            extra_mcp_sessions=snapshot.extra,
            active=snapshot.active,
            armed=state.armed,
            idle_for_seconds=state.idle_for_seconds(),
        )
        if snapshot.active:
            _on_active_tick(state, snapshot)
            continue
        if not state.armed:
            continue
        if _check_idle_expiry(state, snapshot, grace_seconds):
            return
