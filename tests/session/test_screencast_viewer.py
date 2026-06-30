# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

import asyncio

import pytest

from octowright.session.screencast import ScreencastViewer


@pytest.mark.asyncio
async def test_offer_then_get():
    v = ScreencastViewer(fps=1000)
    v.offer(b"a")
    assert await v.get() == b"a"


@pytest.mark.asyncio
async def test_fps_throttle_drops_too_soon():
    t = {"now": 0.0}
    v = ScreencastViewer(fps=10, clock=lambda: t["now"])
    v.offer(b"first")
    t["now"] = 0.05
    v.offer(b"too-soon")
    t["now"] = 0.20
    v.offer(b"ok")
    assert await v.get() == b"first"
    assert await v.get() == b"ok"
    assert v.pending() == 0


@pytest.mark.asyncio
async def test_high_fps_still_throttles_too_soon():
    t = {"now": 0.0}
    v = ScreencastViewer(fps=1000, clock=lambda: t["now"])
    v.offer(b"first")
    t["now"] = 0.0005
    v.offer(b"too-soon")
    t["now"] = 0.002
    v.offer(b"ok")
    assert await v.get() == b"first"
    assert await v.get() == b"ok"
    assert v.pending() == 0


@pytest.mark.asyncio
async def test_concurrent_get_waiters_receive_future_frames():
    t = {"now": 0.0}

    def clock() -> float:
        t["now"] += 0.01
        return t["now"]

    v = ScreencastViewer(fps=1000, clock=clock)
    first = asyncio.create_task(v.get())
    second = asyncio.create_task(v.get())
    await asyncio.sleep(0)

    v.offer(b"first")
    v.offer(b"second")

    assert await asyncio.wait_for(first, timeout=0.1) == b"first"
    assert await asyncio.wait_for(second, timeout=0.1) == b"second"
    assert v.pending() == 0


@pytest.mark.asyncio
async def test_cancelled_get_hands_queued_frame_to_next_waiter():
    v = ScreencastViewer(fps=1000, clock=lambda: 1.0)
    first = asyncio.create_task(v.get())
    second = asyncio.create_task(v.get())
    await asyncio.sleep(0)

    v.offer(b"frame")
    first.cancel()

    assert await asyncio.wait_for(second, timeout=0.1) == b"frame"
    assert v.pending() == 0


@pytest.mark.parametrize("fps", [0, -10])
@pytest.mark.asyncio
async def test_fps_clamps_to_at_least_one(fps):
    t = {"now": 0.0}
    v = ScreencastViewer(fps=fps, clock=lambda: t["now"])
    v.offer(b"first")
    t["now"] = 0.5
    v.offer(b"too-soon")
    t["now"] = 1.1
    v.offer(b"ok")
    assert await v.get() == b"first"
    assert await v.get() == b"ok"
    assert v.pending() == 0


@pytest.mark.asyncio
async def test_queue_full_keeps_latest():
    t = {"now": 0.0}

    def clock() -> float:
        t["now"] += 0.01
        return t["now"]

    v = ScreencastViewer(fps=1000, clock=clock)
    for i in range(200):
        v.offer(str(i).encode())
    assert v.pending() == 4
    drained = []
    while v.pending():
        drained.append(await v.get())
    assert drained == [b"196", b"197", b"198", b"199"]
