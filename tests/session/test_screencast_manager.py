# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

import asyncio

import pytest

from octowright.session import screencast as sc
from tests._operation_gate_fakes import OperationAwareFake


class FakePage:
    def __init__(self):
        self.screencast = FakeScreencast()


class FakeScreencast:
    def __init__(self, *, fail_start=False, fail_stop=False):
        self.started = False
        self.stopped = False
        self._on_frame = None
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    async def start(self, on_frame=None, quality=None):
        if self.fail_start:
            raise RuntimeError("start failed")
        if self.started:
            # Mirrors Playwright: a second start on the same page is an error.
            raise RuntimeError("Screencast is already started")
        self.started = True
        self._on_frame = on_frame

    async def stop(self):
        # Playwright clears its started flag before the channel call, so even a
        # failing stop leaves the page startable again.
        self.started = False
        if self.fail_stop:
            raise RuntimeError("stop failed")
        self.stopped = True
        self._on_frame = None

    def emit(self, data: bytes):
        self._on_frame({"data": data, "viewportWidth": 800, "viewportHeight": 600})


class FakeSession(OperationAwareFake):
    def __init__(self, instance_id="b1"):
        self.instance_id = instance_id
        self.kind = "chromium"
        super().__init__()
        self.page = FakePage()


@pytest.mark.asyncio
async def test_first_viewer_starts_last_viewer_stops():
    mgr = sc.ScreencastManager(FakeSession(), fps=1000, quality=70)
    v1 = await mgr.add_viewer()
    assert mgr._session.page.screencast.started is True
    v2 = await mgr.add_viewer()
    await mgr.remove_viewer(v1)
    assert mgr._session.page.screencast.stopped is False
    await mgr.remove_viewer(v2)
    assert mgr._session.page.screencast.stopped is True


@pytest.mark.asyncio
async def test_frame_fans_out_and_caches_latest():
    sess = FakeSession()
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)
    v1 = await mgr.add_viewer()
    v2 = await mgr.add_viewer()
    sess.page.screencast.emit(b"frame1")
    assert await v1.get() == b"frame1"
    assert await v2.get() == b"frame1"
    assert mgr.latest == b"frame1"


@pytest.mark.asyncio
async def test_registry_shares_one_manager_per_session():
    sess = FakeSession("shared")
    m1, v1 = await sc.acquire_viewer(sess, fps=10, quality=70)
    m2, v2 = await sc.acquire_viewer(sess, fps=10, quality=70)
    assert m1 is m2
    await sc.release_viewer(m1, v1)
    await sc.release_viewer(m2, v2)
    m3, v3 = await sc.acquire_viewer(sess, fps=10, quality=70)
    assert m3 is not m1
    await sc.release_viewer(m3, v3)


@pytest.mark.asyncio
async def test_shared_manager_uses_requested_fps_per_viewer():
    sess = FakeSession("mixed-fps")
    m1, v1 = await sc.acquire_viewer(sess, fps=1, quality=70)
    m2, v2 = await sc.acquire_viewer(sess, fps=1000, quality=70)

    assert m1 is m2
    assert v1._min_gap == 1.0
    assert v2._min_gap == 0.001

    await sc.release_viewer(m1, v1)
    await sc.release_viewer(m2, v2)


@pytest.mark.asyncio
async def test_registry_removes_manager_when_first_start_fails():
    sess = FakeSession("start-fails")
    sess.page.screencast = FakeScreencast(fail_start=True)
    with pytest.raises(RuntimeError, match="start failed"):
        await sc.acquire_viewer(sess, fps=10, quality=70)

    sess.page.screencast = FakeScreencast()
    manager, viewer = await sc.acquire_viewer(sess, fps=10, quality=70)
    assert manager.viewer_count == 1
    await sc.release_viewer(manager, viewer)


@pytest.mark.asyncio
async def test_acquire_cancellation_after_add_viewer_releases_created_viewer():
    started = asyncio.Event()
    allow_start = asyncio.Event()

    class BlockingStartScreencast(FakeScreencast):
        async def start(self, on_frame=None, quality=None):
            await super().start(on_frame=on_frame, quality=quality)
            started.set()
            await allow_start.wait()

    sess = FakeSession("cancel-after-add")
    sess.page.screencast = BlockingStartScreencast()

    task = asyncio.create_task(sc.acquire_viewer(sess, fps=10, quality=70))
    await started.wait()

    await sc._registry_lock.acquire()
    try:
        allow_start.set()
        while sc._managers[sess.instance_id].viewer_count == 0:
            await asyncio.sleep(0)
        task.cancel()
    finally:
        sc._registry_lock.release()

    with pytest.raises(asyncio.CancelledError):
        await task

    manager = sc._managers.get(sess.instance_id)
    assert manager is None
    assert sc._pending_acquires.get(sess.instance_id) is None
    assert sess.page.screencast.stopped is True


@pytest.mark.asyncio
async def test_release_drops_manager_when_final_stop_fails():
    sess = FakeSession("stop-fails")
    sess.page.screencast = FakeScreencast(fail_stop=True)
    manager, viewer = await sc.acquire_viewer(sess, fps=10, quality=70)

    with pytest.raises(RuntimeError, match="stop failed"):
        await sc.release_viewer(manager, viewer)

    assert sc._managers.get(sess.instance_id) is None
    assert manager.viewer_count == 0
    assert manager._started is False
    assert manager._recovery_task is None
    assert sess.page.screencast.stopped is False

    sess.page.screencast.fail_stop = False
    new_manager, new_viewer = await sc.acquire_viewer(sess, fps=10, quality=70)
    assert new_manager is not manager
    await sc.release_viewer(new_manager, new_viewer)


# ─── operation-gate serialization (Task 6) ──────────────────────────────────


async def wait_for_queue_depth(sess, depth):
    async with asyncio.timeout(1):
        while sess.operation_snapshot()["queue_depth"] != depth:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_frame_delivery_does_not_acquire_the_operation_gate():
    """Delivery of frames from an already-started producer must not acquire
    the session operation gate -- it runs on every frame, and gating it would
    serialize video delivery against arbitrary other session work."""
    sess = FakeSession("frame-delivery")
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)
    viewer = sc.ScreencastViewer(fps=1000)
    mgr._viewers.add(viewer)

    mgr._handle_frame({"data": b"jpeg"})

    assert await viewer.get() == b"jpeg"
    assert sess.operation_snapshot()["queue_depth"] == 0
    assert sess.operation_snapshot()["state"] == "open"


@pytest.mark.asyncio
async def test_add_viewer_queues_behind_an_active_session_operation():
    sess = FakeSession("screencast-start-serializes")
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold():
        async with sess.operation("browser_click"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await entered.wait()

    add_task = asyncio.create_task(mgr.add_viewer())
    await wait_for_queue_depth(sess, 1)
    assert sess.page.screencast.started is False

    release.set()
    await holder
    viewer = await add_task
    assert sess.page.screencast.started is True
    await mgr.remove_viewer(viewer)


@pytest.mark.asyncio
async def test_remove_viewer_last_stop_queues_behind_an_active_session_operation():
    sess = FakeSession("screencast-stop-serializes")
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)
    viewer = await mgr.add_viewer()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold():
        async with sess.operation("browser_click"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await entered.wait()

    remove_task = asyncio.create_task(mgr.remove_viewer(viewer))
    await wait_for_queue_depth(sess, 1)
    assert sess.page.screencast.stopped is False

    release.set()
    await holder
    await remove_task
    assert sess.page.screencast.stopped is True


@pytest.mark.asyncio
async def test_rebind_queues_behind_an_active_session_operation():
    sess = FakeSession("screencast-rebind-serializes")
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)
    viewer = await mgr.add_viewer()
    new_page = FakePage()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold():
        async with sess.operation("browser_click"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await entered.wait()

    rebind_task = asyncio.create_task(mgr.rebind(new_page))
    await wait_for_queue_depth(sess, 1)
    assert new_page.screencast.started is False

    release.set()
    await holder
    await rebind_task
    assert new_page.screencast.started is True
    await mgr.remove_viewer(viewer)
