# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Follower-suspension resilience for the bridge watchdog.

When an MCP client (e.g. Codex compaction) SIGSTOPs the follower process, all
its tasks freeze while ``time.monotonic()`` keeps advancing. On resume,
``watch_deadlines`` would see in-flight deadlines already blown by the frozen
wall-clock and fail those requests to the client — even though the leader may
have completed them and a reconnect+resume would recover them. It also wouldn't
proactively replace the now-stale leader session, so the next request times out
on a dead connection.

The fix: ``watch_deadlines`` measures the wall-clock gap between iterations; a
gap far exceeding its sleep interval means the process was suspended, so it
shifts in-flight deadlines forward by the frozen time (don't fail what the
freeze stranded). The reconnect path itself was also fixed to replay the full
``initialize`` + ``notifications/initialized`` handshake so a reconnect after a
freeze no longer leaves the leader session half-initialized (the 400 a real
follower hit).
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.message import SessionMessage

from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import _notification, _request


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_reconnect_replays_initialize_then_initialized() -> None:
    """On reconnect the bridge must replay the FULL handshake — the initialize
    request AND the notifications/initialized that follows it — or the fresh
    leader session stays half-initialized and rejects calls with 400 (the bug a
    real follower hits after a compaction freeze forces a reconnect)."""
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=None, request_timeout_seconds=20.0)
    sup.track_local_message(_request("initialize", "init-1"))
    sup.track_local_message(_notification("notifications/initialized"))

    out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    await sup.replay_initialize(out_send)

    first = out_recv.receive_nowait()
    assert supervisor.message_method(first) == "initialize"
    second = out_recv.receive_nowait()
    assert supervisor.message_method(second) == "notifications/initialized"


def _supervisor(local_write=None):
    return supervisor.BridgeSupervisor(local_read=None, local_write=local_write, request_timeout_seconds=20.0)


def test_handle_suspension_shifts_deadline_by_frozen_time() -> None:
    sup = _supervisor()
    # A navigate in-flight with a 60s budget, deadline set before a 100s freeze.
    sup._in_flight["nav"] = supervisor.InFlightRequest(
        request_id="nav", method="tools/call", started_at=1000.0, deadline=1060.0, timeout=60.0
    )

    sup._handle_suspension(100.0)

    # Frozen time doesn't count against the request: deadline shifted by the gap,
    # so the watchdog won't falsely time it out the instant we resume.
    assert sup._in_flight["nav"].deadline == pytest.approx(1160.0)
    assert sup.suspensions == 1


def test_handle_suspension_shifts_all_inflight() -> None:
    sup = _supervisor()
    sup._in_flight["a"] = supervisor.InFlightRequest(
        request_id="a", method="tools/call", started_at=0.0, deadline=5.0, timeout=5.0
    )
    sup._in_flight["b"] = supervisor.InFlightRequest(
        request_id="b", method="tools/call", started_at=0.0, deadline=10.0, timeout=10.0
    )
    sup._handle_suspension(50.0)
    assert sup._in_flight["a"].deadline == pytest.approx(55.0)
    assert sup._in_flight["b"].deadline == pytest.approx(60.0)
    assert sup.suspensions == 1


@pytest.mark.anyio
async def test_watch_deadlines_detects_suspension_does_not_fail_inflight() -> None:
    """A large inter-iteration gap (process was frozen) must NOT expire in-flight
    requests; it shifts their deadlines forward by the frozen time instead."""
    _send, _ = anyio.create_memory_object_stream[SessionMessage](10)
    out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=out_send, request_timeout_seconds=20.0)
    sup._in_flight["nav"] = supervisor.InFlightRequest(
        request_id="nav", method="tools/call", started_at=1000.0, deadline=1060.0, timeout=60.0
    )

    # Scripted clock: init at 1000, then a 100s jump (freeze), then small ticks.
    ticks = iter([1000.0, 1100.0, 1100.05, 1100.10, 1100.15, 1100.20])

    def fake_now() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 1100.20

    async def fake_sleep(_d: float) -> None:
        await anyio.sleep(0)

    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: sup.watch_deadlines(0.1, None, monotonic=fake_now, sleep=fake_sleep))
        # Give the loop several iterations.
        with anyio.move_on_after(0.5):
            await sup_wait_suspended(sup)
        tg.cancel_scope.cancel()

    assert sup.suspensions >= 1  # freeze detected
    # The in-flight nav was NOT failed to the client, and its deadline was shifted
    # past the frozen wall-clock so the next watchdog tick won't expire it.
    assert "nav" in sup._in_flight
    assert sup._in_flight["nav"].deadline > 1100.20
    with pytest.raises(anyio.WouldBlock):
        out_recv.receive_nowait()


@pytest.mark.anyio
async def test_watch_deadlines_normal_gap_still_expires() -> None:
    """Regression guard: a genuinely-expired request under normal (small-gap)
    iterations is still failed with a bridge error."""
    _send, _ = anyio.create_memory_object_stream[SessionMessage](10)
    out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = supervisor.BridgeSupervisor(local_read=None, local_write=out_send, request_timeout_seconds=20.0)
    sup._in_flight["slow"] = supervisor.InFlightRequest(
        request_id="slow", method="tools/call", started_at=1000.0, deadline=1000.05, timeout=0.05
    )
    ticks = iter([1000.0, 1000.10, 1000.20, 1000.30])

    def fake_now() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 1000.30

    async def fake_sleep(_d: float) -> None:
        await anyio.sleep(0)

    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: sup.watch_deadlines(0.1, None, monotonic=fake_now, sleep=fake_sleep))
        err = await out_recv.receive()
        assert "timed out" in err.message.root.error.message
        tg.cancel_scope.cancel()

    assert sup.suspensions == 0  # small gaps are not suspensions


async def sup_wait_suspended(sup: supervisor.BridgeSupervisor) -> None:
    while sup.suspensions < 1:
        await anyio.sleep(0)
