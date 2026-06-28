# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

import asyncio
from contextlib import suppress

import pytest

from octowright.session import screencast as sc
from tests.session.test_screencast_manager import FakePage, FakeScreencast, FakeSession


@pytest.mark.asyncio
async def test_rebind_starts_screencast_on_new_page():
    sess = FakeSession("rebind-me")
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)
    v = await mgr.add_viewer()
    old = sess.page
    assert old.screencast.started is True

    sess.page = FakePage()
    await mgr.rebind(sess.page)
    assert sess.page.screencast.started is True

    sess.page.screencast.emit(b"post-recovery")
    assert await v.get() == b"post-recovery"
    await mgr.remove_viewer(v)


@pytest.mark.asyncio
async def test_failed_rebind_final_release_cancels_recovery_watcher():
    sess = FakeSession("rebind-start-fails")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    await asyncio.sleep(0)
    task = mgr._recovery_task
    assert task is not None
    assert task.done() is False

    failed_page = FakePage()
    failed_page.screencast = FakeScreencast(fail_start=True)
    sess.page = failed_page

    try:
        with pytest.raises(RuntimeError, match="start failed"):
            await mgr.rebind(failed_page)

        await sc.release_viewer(mgr, viewer)

        assert sc._managers.get(sess.instance_id) is None
        assert mgr._recovery_task is None
        assert task.done() is True
    finally:
        if mgr._recovery_task is not None:
            mgr._recovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await mgr._recovery_task
        async with sc._registry_lock:
            sc._managers.pop(sess.instance_id, None)
            sc._pending_acquires.pop(sess.instance_id, None)
