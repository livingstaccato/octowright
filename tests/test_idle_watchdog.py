# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Behavioural tests for ``idle_watchdog``.

The watchdog only watches two things — pool.list_sessions() and
scenario_pool.list_live(). Stub them so the tests don't need real browsers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from octowright.idle_watchdog import idle_watchdog


class _FakeClock:
    """The watchdog's time source and poll wait, advanced only by the watchdog's own polls.

    Asserting *when* the watchdog fired against a real ``asyncio.sleep`` timed
    the runner as much as the watchdog: a loaded macOS runner woke the task
    that re-armed the pool too late, the watchdog fired on the countdown the
    re-arm should have cleared, and ``elapsed >= 0.16`` failed at 0.138s. Here
    a poll advances time by exactly its interval and then applies every
    scheduled change due by then, so each verdict is exact and independent of
    scheduling. Integral times keep the float arithmetic exact.
    """

    def __init__(self, schedule: dict[float, Callable[[], object]]) -> None:
        self._now = 0.0
        self._pending = sorted(schedule.items())

    def now(self) -> float:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self._now += seconds
        while self._pending and self._pending[0][0] <= self._now:
            self._pending.pop(0)[1]()
        await asyncio.sleep(0)  # still yield, as the real wait does


def _stub(sessions: list, scenarios: list) -> SimpleNamespace:
    """A lightweight pool stub exposing only the methods the watchdog reads."""
    return SimpleNamespace(
        list_sessions=lambda: list(sessions),
        list_live=lambda: list(scenarios),
    )


@pytest.mark.asyncio
async def test_watchdog_does_not_fire_before_pool_is_used() -> None:
    """Fresh server, no sessions ever — watchdog must not fire even after grace elapses."""
    pool = _stub([], [])
    scenarios = _stub([], [])
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            idle_watchdog(pool, scenarios, grace_seconds=0.05, poll_seconds=0.01),
            timeout=0.3,
        )


@pytest.mark.asyncio
async def test_watchdog_fires_after_pool_drains() -> None:
    """Once a session existed and is gone for grace_seconds, the watchdog returns."""
    sessions: list = [{"instance_id": "a"}]
    pool = _stub(sessions, [])
    scenarios = _stub([], [])

    async def _drain_after_short_delay() -> None:
        await asyncio.sleep(0.05)
        sessions.clear()

    drainer = asyncio.create_task(_drain_after_short_delay())
    await asyncio.wait_for(
        idle_watchdog(pool, scenarios, grace_seconds=0.05, poll_seconds=0.01),
        timeout=1.0,
    )
    await drainer


@pytest.mark.asyncio
async def test_watchdog_resets_grace_when_new_session_appears() -> None:
    """A new session during the grace window pushes the timer back to zero.

    Sessions: present until t=3, gone until t=6, back until t=16, then gone.
    With the reset the countdown restarts at t=16 and fires at t=16+8=24;
    a watchdog that kept the first countdown (started at t=3) fires at t=16.
    """
    sessions: list = [{"instance_id": "a"}]
    clock = _FakeClock(
        {
            3: sessions.clear,
            6: lambda: sessions.append({"instance_id": "b"}),  # re-arm during grace
            16: sessions.clear,  # final drain
        }
    )
    await asyncio.wait_for(
        idle_watchdog(
            _stub(sessions, []), _stub([], []), grace_seconds=8, poll_seconds=1, clock=clock.now, sleep=clock.sleep
        ),
        timeout=5.0,
    )
    assert clock.now() == 24


@pytest.mark.asyncio
async def test_watchdog_arm_immediately_fires_without_prior_activity() -> None:
    """When arm_immediately=True, the daemon mode exits even without ever seeing a session."""
    pool = _stub([], [])
    scenarios = _stub([], [])
    await asyncio.wait_for(
        idle_watchdog(pool, scenarios, grace_seconds=0.05, poll_seconds=0.01, arm_immediately=True),
        timeout=1.0,
    )


@pytest.mark.asyncio
async def test_watchdog_arm_immediately_resets_on_activity() -> None:
    """Even with arm_immediately, an active session pushes the timer back.

    Armed and idle from the first tick (t=1); a session from t=2 to t=12
    restarts the countdown at t=12, so it fires at t=12+5=17. Without the
    reset the countdown from t=1 fires the first idle tick after it, t=12.
    """
    sessions: list = []
    clock = _FakeClock({2: lambda: sessions.append({"instance_id": "a"}), 12: sessions.clear})
    await asyncio.wait_for(
        idle_watchdog(
            _stub(sessions, []),
            _stub([], []),
            grace_seconds=5,
            poll_seconds=1,
            arm_immediately=True,
            clock=clock.now,
            sleep=clock.sleep,
        ),
        timeout=5.0,
    )
    assert clock.now() == 17


@pytest.mark.asyncio
async def test_watchdog_treats_live_scenario_as_active() -> None:
    """A live scenario alone (no browsers) keeps the watchdog quiet."""
    scenarios_list: list = [{"scenario_id": "s"}]
    pool = _stub([], [])
    scenarios = _stub([], scenarios_list)

    # Even though sessions never existed, a scenario being live arms+holds the watchdog.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            idle_watchdog(pool, scenarios, grace_seconds=0.05, poll_seconds=0.01),
            timeout=0.3,
        )

    # Drain the scenario partway through a fresh watchdog and confirm it fires.
    async def _drain_scenario_after_short_delay() -> None:
        await asyncio.sleep(0.05)
        scenarios_list.clear()

    drainer = asyncio.create_task(_drain_scenario_after_short_delay())
    await asyncio.wait_for(
        idle_watchdog(pool, scenarios, grace_seconds=0.05, poll_seconds=0.01),
        timeout=1.0,
    )
    await drainer
