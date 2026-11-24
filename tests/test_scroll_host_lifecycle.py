# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Scroll's host deadline, ordering and resource ownership with a stalled driver."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from octowright.session.timeouts import SessionCallTimeoutError
from tests.test_emulation_primitives import _session

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Observer:
    def __init__(self, *, block: str = "") -> None:
        self.events: list[str] = []
        self.tasks: list[asyncio.Task | None] = []
        self.waiting = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release = asyncio.Event()
        self.block = block
        self.active = False
        self.closed = False

    def arm(self) -> None:
        if not self.closed:
            self.active = True

    async def evaluate(self, expression: str) -> bool:
        phase = "cancel" if "cancel" in expression else ("arm" if "arm" in expression else "wait")
        self.events.append(phase)
        self.tasks.append(asyncio.current_task())
        if phase == "arm":
            self.arm()
        if phase == "cancel":
            self.closed = True
            self.active = False
            self.cleaning.set()
        if phase == "wait":
            self.waiting.set()
            await self.release.wait()
        if self.block == phase:
            await asyncio.Event().wait()
        return phase == "wait"

    async def dispose(self) -> None:
        self.events.append("dispose")
        if self.block == "dispose":
            await asyncio.Event().wait()


def _install(session, monkeypatch, observer, *, wheel_error=False, dispatch_delay=0):
    async def setup(expression):
        observer.events.append("allocate")
        return observer

    async def wheel(dx, dy):
        observer.events.append("wheel")
        observer.tasks.append(asyncio.current_task())
        await asyncio.sleep(dispatch_delay)
        if wheel_error and (dx or dy):
            raise RuntimeError("wheel failed")
        observer.events.append("delivered")

    original_metrics = session.page.evaluate

    async def metrics(expression):
        observer.events.append("metrics")
        return await original_metrics(expression)

    monkeypatch.setattr(session.page, "evaluate_handle", setup)
    monkeypatch.setattr(session.page.mouse, "wheel", wheel)
    monkeypatch.setattr(session.page, "evaluate", metrics)


def _scroll_records(session):
    return [
        json.loads(row) for row in session.log_path.read_text().splitlines() if json.loads(row)["action"] == "scroll"
    ]


@pytest.mark.parametrize("generic_budget", ["off", "0.01"])
async def test_host_deadline_returns_unsettled_without_generic_guard(tmp_path, monkeypatch, generic_budget):
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", generic_budget)
    session = _session(tmp_path)
    observer = _Observer()
    _install(session, monkeypatch, observer)
    started = time.monotonic()
    async with asyncio.timeout(2):
        result = await session.scroll(delta_x=0, delta_y=300)
    assert 0.8 < time.monotonic() - started < 2
    assert result["ok"] is True
    assert result["settled"] is False
    assert observer.events == ["allocate", "arm", "wheel", "delivered", "wait", "cancel", "dispose", "metrics"]
    assert len(_scroll_records(session)) == 1


async def test_settling_budget_starts_after_slow_wheel_dispatch(tmp_path, monkeypatch):
    session = _session(tmp_path)
    observer = _Observer()
    _install(session, monkeypatch, observer, dispatch_delay=1.1)
    started = time.monotonic()
    async with asyncio.timeout(3):
        result = await session.scroll(delta_x=0, delta_y=300)
    assert time.monotonic() - started >= 2
    assert result["settled"] is False
    assert len(_scroll_records(session)) == 1


async def test_observation_wait_and_metrics_keep_gate_owner_and_order(tmp_path, monkeypatch):
    session = _session(tmp_path)
    observer = _Observer()
    observer.release.set()
    _install(session, monkeypatch, observer)
    result = await session.scroll(delta_x=0, delta_y=300)
    assert result["settled"] is True
    assert observer.events == ["allocate", "arm", "wheel", "delivered", "wait", "cancel", "dispose", "metrics"]
    assert all(task is asyncio.current_task() for task in observer.tasks)


@pytest.mark.parametrize("phase", ["wheel_error", "cancel"])
async def test_cleanup_runs_on_wheel_failure_and_cancellation(tmp_path, monkeypatch, phase):
    session = _session(tmp_path)
    observer = _Observer()
    _install(session, monkeypatch, observer, wheel_error=phase == "wheel_error")
    if phase == "wheel_error":
        with pytest.raises(RuntimeError, match="wheel failed"):
            await session.scroll(delta_x=0, delta_y=300)
    else:
        task = asyncio.create_task(session.scroll(delta_x=0, delta_y=300))
        await observer.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert observer.events[-2:] == ["cancel", "dispose"]
    assert len(_scroll_records(session)) == (0 if phase == "wheel_error" else 1)
    assert (await session.scroll(delta_x=0, delta_y=0))["ok"]


@pytest.mark.parametrize("phase", ["cancel", "dispose"])
async def test_stalled_cleanup_is_independently_bounded(tmp_path, monkeypatch, phase):
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "off")
    session = _session(tmp_path)
    observer = _Observer(block=phase)
    observer.release.set()
    _install(session, monkeypatch, observer)
    async with asyncio.timeout(2):
        result = await session.scroll(delta_x=0, delta_y=300)
    assert observer.events[-3:] == ["cancel", "dispose", "metrics"]
    assert result["ok"]


async def test_observer_setup_is_bounded_without_generic_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "off")
    session = _session(tmp_path)

    async def stalled_setup(expression):
        await asyncio.Event().wait()

    monkeypatch.setattr(session.page, "evaluate_handle", stalled_setup)
    async with asyncio.timeout(2):
        with pytest.raises(SessionCallTimeoutError):
            await session.scroll(delta_x=0, delta_y=300)
    assert not session.page.mouse.moves
    assert not _scroll_records(session)


@pytest.mark.parametrize("dispose_stalls", [False, True])
async def test_cancellation_during_cleanup_still_attempts_bounded_disposal(tmp_path, monkeypatch, dispose_stalls):
    session = _session(tmp_path)
    observer = _Observer(block="cancel")
    observer.release.set()
    _install(session, monkeypatch, observer)
    dispose = observer.dispose

    async def maybe_stalled_dispose():
        await dispose()
        if dispose_stalls:
            await asyncio.Event().wait()

    monkeypatch.setattr(observer, "dispose", maybe_stalled_dispose)
    task = asyncio.create_task(session.scroll(delta_x=0, delta_y=300))
    await observer.cleaning.wait()
    started = time.monotonic()
    task.cancel()
    async with asyncio.timeout(2):
        with pytest.raises(asyncio.CancelledError):
            await task
    assert time.monotonic() - started < 1.8
    assert observer.events[-2:] == ["cancel", "dispose"]
    assert len(_scroll_records(session)) == 1
    assert (await session.scroll(delta_x=0, delta_y=0))["ok"]


@pytest.mark.parametrize("phase", ["wait", "metrics"])
async def test_observation_error_after_delivery_returns_success_without_repeating_input(tmp_path, monkeypatch, phase):
    session = _session(tmp_path)
    observer = _Observer()
    observer.release.set()
    _install(session, monkeypatch, observer)
    evaluate = observer.evaluate

    async def failed_observation(expression):
        if "done" in expression:
            raise RuntimeError("execution context destroyed")
        return await evaluate(expression)

    async def failed_metrics(expression):
        raise RuntimeError("execution context destroyed")

    if phase == "wait":
        monkeypatch.setattr(observer, "evaluate", failed_observation)
    else:
        monkeypatch.setattr(session.page, "evaluate", failed_metrics)
    result = await session.scroll(delta_x=0, delta_y=300)
    assert result["ok"] is True
    assert result["settled"] is False
    assert result["y"] == (640 if phase == "wait" else None)
    if phase == "metrics":
        assert all(result[key] is None for key in ("x", "y", "height", "innerHeight"))
    assert observer.events.count("delivered") == 1
    assert len(_scroll_records(session)) == 1
    assert "dispose" in observer.events


async def test_truthy_page_value_does_not_claim_boolean_stability(tmp_path, monkeypatch):
    session = _session(tmp_path)
    observer = _Observer()
    observer.release.set()
    _install(session, monkeypatch, observer)
    evaluate = observer.evaluate

    async def forged_observation(expression):
        return "truthy" if "done" in expression else await evaluate(expression)

    monkeypatch.setattr(observer, "evaluate", forged_observation)
    assert (await session.scroll(delta_x=0, delta_y=300))["settled"] is False


@pytest.mark.parametrize("interrupt", ["timeout", "cancel"])
@pytest.mark.parametrize("late_execution", [False, True])
async def test_interrupted_arm_cleans_observer_even_when_driver_ack_is_late(
    tmp_path, monkeypatch, interrupt, late_execution
):
    session = _session(tmp_path)
    observer = _Observer()
    _install(session, monkeypatch, observer)
    evaluate = observer.evaluate
    release_remote = asyncio.Event()
    remote_started = asyncio.Event()
    remote_calls = []

    async def remote_arm():
        remote_started.set()
        if late_execution:
            await release_remote.wait()
        await evaluate("state => state.arm()")
        if not late_execution:
            await release_remote.wait()

    async def delayed_ack(expression):
        if "arm" not in expression:
            return await evaluate(expression)
        remote = asyncio.create_task(remote_arm())
        remote_calls.append(remote)
        # A timed-out Playwright request can still execute in the browser.
        return await asyncio.shield(remote)

    monkeypatch.setattr(observer, "evaluate", delayed_ack)
    task = asyncio.create_task(session.scroll(delta_x=0, delta_y=300))
    try:
        async with asyncio.timeout(3):
            await remote_started.wait()
            if interrupt == "cancel":
                task.cancel()
            with pytest.raises(asyncio.CancelledError if interrupt == "cancel" else SessionCallTimeoutError):
                await task
        assert observer.closed is True
        assert observer.active is False
        assert "dispose" in observer.events
        assert "wheel" not in observer.events
        assert not _scroll_records(session)
    finally:
        release_remote.set()
        async with asyncio.timeout(1):
            await asyncio.gather(*remote_calls)
    assert observer.active is False, "late arm must respect the cancelled observer's terminal state"


@pytest.mark.parametrize("interrupt", ["timeout", "cancel"])
async def test_interrupted_inert_allocation_cannot_arm_after_late_acknowledgement(tmp_path, monkeypatch, interrupt):
    session = _session(tmp_path)
    observer = _Observer()
    allocated = asyncio.Event()
    release_remote = asyncio.Event()
    remote_calls = []

    async def allocate_remotely():
        allocated.set()
        await release_remote.wait()
        return observer

    async def delayed_allocation(expression):
        remote = asyncio.create_task(allocate_remotely())
        remote_calls.append(remote)
        return await asyncio.shield(remote)

    monkeypatch.setattr(session.page, "evaluate_handle", delayed_allocation)
    task = asyncio.create_task(session.scroll(delta_x=0, delta_y=300))
    try:
        async with asyncio.timeout(2):
            await allocated.wait()
            if interrupt == "cancel":
                task.cancel()
            with pytest.raises(asyncio.CancelledError if interrupt == "cancel" else SessionCallTimeoutError):
                await task
    finally:
        release_remote.set()
        async with asyncio.timeout(1):
            await asyncio.gather(*remote_calls)
    # A handle whose reply was lost can survive until context teardown, but
    # allocation has no active page resources and must never receive arm().
    assert observer.active is False
    assert observer.events == []
    assert not session.page.mouse.moves
    assert not _scroll_records(session)


async def test_cancellation_during_post_delivery_metrics_still_propagates(tmp_path, monkeypatch):
    session = _session(tmp_path)
    observer = _Observer()
    observer.release.set()
    _install(session, monkeypatch, observer)
    measuring = asyncio.Event()

    async def blocked_metrics(expression):
        measuring.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(session.page, "evaluate", blocked_metrics)
    task = asyncio.create_task(session.scroll(delta_x=0, delta_y=300))
    async with asyncio.timeout(2):
        await measuring.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(_scroll_records(session)) == 1
    assert observer.events.count("delivered") == 1


async def test_metrics_stall_after_input_returns_null_metrics_without_generic_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "off")
    session = _session(tmp_path)
    observer = _Observer()
    observer.release.set()
    _install(session, monkeypatch, observer)

    async def blocked_metrics(expression):
        await asyncio.Event().wait()

    monkeypatch.setattr(session.page, "evaluate", blocked_metrics)
    async with asyncio.timeout(2):
        result = await session.scroll(delta_x=0, delta_y=300)
    assert result["ok"] is True
    assert result["settled"] is False
    assert all(result[key] is None for key in ("x", "y", "height", "innerHeight"))
    assert len(_scroll_records(session)) == 1
