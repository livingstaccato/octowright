# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Scroll completion versus the real host timeout, without wall-clock sleeps."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator

import pytest

from tests.test_emulation_primitives import _session
from tests.test_scroll_host_lifecycle import _Observer, _scroll_records


class _Scheduler:
    """Run real asyncio callbacks and timeout cancellation at chosen loop times."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.now = 100.0

    def time(self) -> float:
        return self.now

    def turn(self) -> None:
        # A ready stop callback makes the selector nonblocking. Timers, ready
        # tasks and cancellation still go through asyncio's actual scheduler.
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()

    def until(self, predicate: Callable[[], bool]) -> None:
        for _ in range(20):
            if predicate():
                return
            self.turn()
        assert predicate(), "scroll did not reach the expected scheduler checkpoint"


@pytest.fixture
def scheduler(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Scheduler]:
    scheduler = _Scheduler()
    monkeypatch.setattr(scheduler.loop, "time", scheduler.time)
    try:
        yield scheduler
    finally:
        tasks = asyncio.all_tasks(scheduler.loop)
        for task in tasks:
            task.cancel()
        try:
            scheduler.until(lambda: all(task.done() for task in tasks))
        finally:
            scheduler.loop.close()


@pytest.mark.parametrize(
    ("completion", "expected_settled"),
    [
        pytest.param("before", True, id="done-before-expiry"),
        pytest.param("ready-at", True, id="done-already-ready-at-expiry"),
        pytest.param("timer-at", False, id="done-timer-due-at-expiry"),
        pytest.param("after", False, id="done-after-expiry"),
    ],
)
def test_scroll_deadline_completion_order(tmp_path, monkeypatch, scheduler, completion, expected_settled):
    # Disabling the generic guard must not disable scroll's explicit budget.
    # Moving that budget before wheel acknowledgement would also fail here.
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "off")
    session = _session(tmp_path)
    observer = _Observer()
    dispatching = asyncio.Event()
    acknowledge = asyncio.Event()

    async def allocate(expression):
        observer.events.append("allocate")
        return observer

    async def wheel(dx, dy):
        observer.events.append("wheel")
        dispatching.set()
        await acknowledge.wait()
        observer.events.append("delivered")

    monkeypatch.setattr(session.page, "evaluate_handle", allocate)
    monkeypatch.setattr(session.page.mouse, "wheel", wheel)
    task = scheduler.loop.create_task(session.scroll(delta_x=0, delta_y=300))
    scheduler.until(dispatching.is_set)
    scheduler.now = 105.0
    scheduler.turn()
    assert not task.done()
    assert not _scroll_records(session)

    acknowledge.set()
    scheduler.until(observer.waiting.is_set)
    assert len(_scroll_records(session)) == 1
    assert observer.active

    if completion == "before":
        scheduler.loop.call_at(105.999, observer.release.set)
        scheduler.now = 105.999
    elif completion == "ready-at":
        # Task continuation is ready BEFORE the due deadline is dispatched.
        # It completes the observation and cancels its timeout handle first.
        scheduler.now = 106.0
        observer.release.set()
    else:
        # A due completion timer only schedules the task continuation. The
        # due timeout cancels it before that continuation can resume, even if
        # asyncio chooses to dispatch the completion timer first in a tie.
        scheduler.loop.call_at(106.0 if completion == "timer-at" else 106.001, observer.release.set)
        scheduler.now = 106.0

    scheduler.until(task.done)
    result = task.result()
    assert result["ok"] is True
    assert result["settled"] is expected_settled
    assert result["y"] == 640
    assert observer.events == ["allocate", "arm", "wheel", "delivered", "wait", "cancel", "dispose"]
    assert observer.closed
    assert not observer.active
    assert len(_scroll_records(session)) == 1

    # A late driver completion cannot repeat input, recording or cleanup.
    scheduler.now = 106.001
    scheduler.turn()
    assert observer.release.is_set()
    assert observer.events.count("delivered") == 1
    assert observer.events.count("cancel") == observer.events.count("dispose") == 1
    assert len(_scroll_records(session)) == 1

    # Expiry must release the production operation gate for the next caller.
    followup = scheduler.loop.create_task(session.scroll(delta_x=0, delta_y=0))
    scheduler.until(followup.done)
    assert followup.result()["ok"] is True
