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
from types import SimpleNamespace

import pytest

from octowright.idle_watchdog import idle_watchdog


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
    """A new session during the grace window pushes the timer back to zero."""
    sessions: list = [{"instance_id": "a"}]
    pool = _stub(sessions, [])
    scenarios = _stub([], [])

    async def _flap() -> None:
        await asyncio.sleep(0.03)
        sessions.clear()
        await asyncio.sleep(0.03)
        sessions.append({"instance_id": "b"})  # re-arm during grace
        await asyncio.sleep(0.10)
        sessions.clear()  # final drain

    flapper = asyncio.create_task(_flap())
    # If the watchdog ignored the re-armed session, it would fire too early.
    # grace=0.08 means without the reset it'd fire ~t=0.11, but the reset
    # pushes its earliest fire to ~t=0.16+0.08 = 0.24.
    start = asyncio.get_event_loop().time()
    await asyncio.wait_for(
        idle_watchdog(pool, scenarios, grace_seconds=0.08, poll_seconds=0.01),
        timeout=2.0,
    )
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.16
    await flapper


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
