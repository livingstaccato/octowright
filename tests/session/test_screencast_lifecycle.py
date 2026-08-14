# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Lifecycle edges of the screencast manager: which page the producer is bound
to, what happens when a rebind fails, and how session events end a stream.

`test_screencast_manager.py` covers the happy path (start on first viewer, stop
on last) and `test_screencast_rebind.py` the crash-recovery rebind. This file
pins the cases where ``session.page`` and the *bound* page diverge:

- the active page changes under a live preview (``page_switch`` / ``page_close``)
- a rebind's ``screencast.start`` fails and leaves viewers with no producer
- a background-page crash recovers, so the recovered event names a page the
  manager is already casting
- the session closes while a viewer is still attached
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from octowright.browser_pool.events import SessionClosedEvent, SessionRecoveredEvent
from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.session import screencast as sc
from tests.session.test_screencast_manager import FakePage, FakeScreencast, FakeSession


async def _await_watcher_subscription() -> None:
    """Let the manager's recovery watcher task reach ``bus.subscribe()``.

    The bus drops events published before a subscriber exists, so a test that
    publishes immediately after ``acquire_viewer`` would race the watcher's
    first step.
    """
    for _ in range(100):
        if session_event_bus.subscriber_count:
            return
        await asyncio.sleep(0)
    raise AssertionError("recovery watcher never subscribed")


async def _drain_registry(instance_id: str) -> None:
    async with sc._registry_lock:
        sc._managers.pop(instance_id, None)
        sc._pending_acquires.pop(instance_id, None)


@pytest.mark.asyncio
async def test_remove_viewer_stops_the_bound_page_not_the_new_active_page() -> None:
    """A raw ``session.page`` swap must not misdirect the final stop: the
    producer lives on the page the manager started, so that is what gets stopped."""
    sess = FakeSession("bound-page")
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)
    viewer = await mgr.add_viewer()
    bound = sess.page

    other = FakePage()
    sess.page = other  # e.g. an unnoticed switch_page

    await mgr.remove_viewer(viewer)

    assert bound.screencast.stopped is True
    assert other.screencast.stopped is False


@pytest.mark.asyncio
async def test_active_page_change_rebinds_the_live_screencast() -> None:
    """``notify_active_page`` moves the producer to the new active page so the
    preview shows the tab the session is actually on."""
    sess = FakeSession("active-page-change")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    old = sess.page
    try:
        new_page = FakePage()
        sess.page = new_page
        await sc.notify_active_page(sess.instance_id, new_page)

        assert old.screencast.stopped is True
        assert new_page.screencast.started is True

        new_page.screencast.emit(b"new-tab")
        assert await viewer.get() == b"new-tab"
    finally:
        await sc.release_viewer(mgr, viewer)
        await _drain_registry(sess.instance_id)


@pytest.mark.asyncio
async def test_notify_active_page_without_a_live_manager_is_a_noop() -> None:
    await sc.notify_active_page("no-such-session", FakePage())


@pytest.mark.asyncio
async def test_notify_active_page_swallows_a_failed_rebind() -> None:
    """A page switch must not fail because the screencast could not follow it."""
    sess = FakeSession("rebind-swallowed")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    try:
        broken = FakePage()
        broken.screencast = FakeScreencast(fail_start=True)
        sess.page = broken

        await sc.notify_active_page(sess.instance_id, broken)

        with pytest.raises(sc.ScreencastEnded):
            await viewer.get()
    finally:
        await sc.release_viewer(mgr, viewer)
        await _drain_registry(sess.instance_id)


@pytest.mark.asyncio
async def test_failed_rebind_ends_viewers_so_the_client_can_fall_back() -> None:
    """A rebind whose start fails leaves no producer; viewers must be woken with
    ``ScreencastEnded`` instead of blocking on a stream that will never resume."""
    sess = FakeSession("rebind-ends-viewers")
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)
    viewer = await mgr.add_viewer()

    broken = FakePage()
    broken.screencast = FakeScreencast(fail_start=True)

    with pytest.raises(RuntimeError, match="start failed"):
        await mgr.rebind(broken)

    with pytest.raises(sc.ScreencastEnded):
        await viewer.get()

    await mgr.remove_viewer(viewer)


@pytest.mark.asyncio
async def test_rebind_to_the_already_bound_page_is_a_noop() -> None:
    """A background-tab crash recovery names the unchanged active page; starting
    a second screencast on it would raise, so the manager must skip it."""
    sess = FakeSession("same-page-rebind")
    mgr = sc.ScreencastManager(sess, fps=1000, quality=70)
    viewer = await mgr.add_viewer()
    bound = sess.page

    await mgr.rebind(bound)

    assert mgr._started is True
    assert bound.screencast.stopped is False
    bound.screencast.emit(b"still-live")
    assert await viewer.get() == b"still-live"

    await mgr.remove_viewer(viewer)
    assert bound.screencast.stopped is True


@pytest.mark.asyncio
async def test_recovered_event_for_a_background_page_leaves_the_stream_intact() -> None:
    sess = FakeSession("background-crash")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    bound = sess.page
    try:
        await _await_watcher_subscription()
        session_event_bus.publish_nowait(
            SessionRecoveredEvent(
                instance_id=sess.instance_id,
                kind="chromium",
                label=None,
                profile=None,
                outcome="recovered",
                attempts=1,
                log_path="/x/fake.jsonl",  # fake event payload; never opened
            )
        )
        for _ in range(10):
            await asyncio.sleep(0)

        assert mgr._started is True
        bound.screencast.emit(b"unaffected")
        assert await viewer.get() == b"unaffected"
    finally:
        await sc.release_viewer(mgr, viewer)
        await _drain_registry(sess.instance_id)


@pytest.mark.asyncio
async def test_session_closed_event_stops_the_producer_and_ends_viewers() -> None:
    sess = FakeSession("closed-while-watching")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    bound = sess.page
    try:
        await _await_watcher_subscription()
        session_event_bus.publish_nowait(
            SessionClosedEvent(
                instance_id=sess.instance_id,
                kind="chromium",
                label=None,
                profile=None,
                reason="user_close",
                log_path="/x/fake.jsonl",  # fake event payload; never opened
            )
        )
        with pytest.raises(sc.ScreencastEnded):
            await asyncio.wait_for(viewer.get(), timeout=2)

        assert mgr._started is False
        assert bound.screencast.stopped is True
    finally:
        with suppress(Exception):
            await sc.release_viewer(mgr, viewer)
        await _drain_registry(sess.instance_id)


@pytest.mark.asyncio
async def test_session_closed_event_terminates_even_when_the_gate_is_already_closed() -> None:
    """``terminate()`` must not try to acquire the session operation gate: by
    the time an external close reaches here the gate itself may already be
    ``closed``, and entering it would raise instead of releasing the
    screencast producer and waking the viewers -- exactly the one thing this
    path must still do."""
    sess = FakeSession("gate-already-closed")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    bound = sess.page
    await _await_watcher_subscription()
    sess._test_operation_gate.mark_closed_external()

    session_event_bus.publish_nowait(
        SessionClosedEvent(
            instance_id=sess.instance_id,
            kind="chromium",
            label=None,
            profile=None,
            reason="user_close",
            log_path="/x/fake.jsonl",  # fake event payload; never opened
        )
    )
    with pytest.raises(sc.ScreencastEnded):
        await asyncio.wait_for(viewer.get(), timeout=2)

    assert mgr._started is False
    assert bound.screencast.stopped is True

    # release_viewer (the WS endpoint's `finally`, and the ONLY production
    # path that reclaims a manager) must still succeed and fully reclaim the
    # manager even though the session's operation gate is already closed --
    # not raise SessionClosedError and strand a manager holding the closed
    # BrowserSession for the life of the process.
    await sc.release_viewer(mgr, viewer)
    assert sc._managers.get(sess.instance_id) is None
    assert mgr.viewer_count == 0


@pytest.mark.asyncio
async def test_close_event_published_before_the_watcher_task_runs_is_not_lost() -> None:
    """The bus drops events with no subscriber, and ``create_task`` only
    *schedules* the watcher — so subscribing inside the task would lose a close
    that lands before its first step, leaving the viewer blocked forever.

    Nothing in this test yields between ``acquire_viewer`` and the publish, so
    the watcher task provably has not run yet.
    """
    sess = FakeSession("close-before-watcher-runs")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    try:
        assert mgr._recovery_task is not None
        assert mgr._recovery_task.done() is False
        assert session_event_bus.subscriber_count >= 1, "subscription must exist before the task runs"

        session_event_bus.publish_nowait(
            SessionClosedEvent(
                instance_id=sess.instance_id,
                kind="chromium",
                label=None,
                profile=None,
                reason="user_close",
                log_path="/x/fake.jsonl",  # fake event payload; never opened
            )
        )

        with pytest.raises(sc.ScreencastEnded):
            await asyncio.wait_for(viewer.get(), timeout=2)
    finally:
        with suppress(Exception):
            await sc.release_viewer(mgr, viewer)
        await _drain_registry(sess.instance_id)


@pytest.mark.asyncio
async def test_release_drops_the_bus_subscription() -> None:
    """The subscription now outlives the task creation, so the release path must
    close it or every finished preview leaks a subscriber."""
    before = session_event_bus.subscriber_count
    sess = FakeSession("subscription-cleanup")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    assert session_event_bus.subscriber_count == before + 1

    await sc.release_viewer(mgr, viewer)
    await _drain_registry(sess.instance_id)

    assert session_event_bus.subscriber_count == before


@pytest.mark.asyncio
async def test_session_closed_event_for_another_session_is_ignored() -> None:
    sess = FakeSession("other-session-close")
    mgr, viewer = await sc.acquire_viewer(sess, fps=1000, quality=70)
    try:
        await _await_watcher_subscription()
        session_event_bus.publish_nowait(
            SessionClosedEvent(
                instance_id="someone-else",
                kind="chromium",
                label=None,
                profile=None,
                reason="user_close",
                log_path="/x/fake.jsonl",  # fake event payload; never opened
            )
        )
        for _ in range(10):
            await asyncio.sleep(0)

        assert mgr._started is True
        sess.page.screencast.emit(b"still-here")
        assert await viewer.get() == b"still-here"
    finally:
        await sc.release_viewer(mgr, viewer)
        await _drain_registry(sess.instance_id)
