# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from typing import Literal

import pytest

from octowright.session import operation_gate
from octowright.session.operation_gate import (
    USE_DEFAULT,
    OperationGateInvariantError,
    SessionBusyTimeoutError,
    SessionClosedError,
    SessionClosingError,
    SessionOperationGate,
    resolve_operation_queue_timeout_seconds,
    validate_operation_name,
)


def test_operation_timeout_resolution_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", "41.5")
    assert resolve_operation_queue_timeout_seconds(None) == 41.5
    assert resolve_operation_queue_timeout_seconds(7.0) == 7.0


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "nope"])
def test_operation_timeout_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS"):
        resolve_operation_queue_timeout_seconds(None)


def test_gate_errors_are_distinct_runtime_errors() -> None:
    errors = {
        SessionBusyTimeoutError,
        SessionClosingError,
        SessionClosedError,
        OperationGateInvariantError,
    }
    assert len(errors) == 4
    assert all(issubclass(error, RuntimeError) for error in errors)


def test_operation_names_are_fixed_identifiers() -> None:
    assert validate_operation_name("browser_click") == "browser_click"
    for unsafe in ("#password", "https://secret.test", "user supplied", "", "a" * 65):
        with pytest.raises(ValueError, match="fixed identifier"):
            validate_operation_name(unsafe)


class _LogCapture:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def debug(self, event: str, **kw: object) -> None:
        self.events.append((event, kw))

    def info(self, event: str, **kw: object) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: object) -> None:
        self.events.append((event, kw))

    def error(self, event: str, **kw: object) -> None:
        self.events.append((event, kw))


class _MetricCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, str] | None]] = []

    def record(self, value: float, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((value, attributes))

    def add(self, amount: int, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((amount, attributes))


async def wait_for_queue_depth(gate: SessionOperationGate, depth: int) -> None:
    async with asyncio.timeout(1):
        while gate.snapshot()["queue_depth"] != depth:
            await asyncio.sleep(0)


async def enter_and_signal(gate: SessionOperationGate, name: str, entered: asyncio.Event) -> None:
    async with gate.operation(name):
        entered.set()


@pytest.mark.asyncio
async def test_fifo_waiters_run_in_arrival_order() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()
    order: list[str] = []

    async def owner() -> None:
        async with gate.operation("owner"):
            owner_entered.set()
            await release_owner.wait()

    async def waiter(name: Literal["first", "second"]) -> None:
        async with gate.operation(name):
            order.append(name)

    owner_task = asyncio.create_task(owner())
    await owner_entered.wait()
    first = asyncio.create_task(waiter("first"))
    await wait_for_queue_depth(gate, 1)
    second = asyncio.create_task(waiter("second"))
    await wait_for_queue_depth(gate, 2)
    release_owner.set()
    await asyncio.gather(owner_task, first, second)
    assert order == ["first", "second"]


@pytest.mark.asyncio
async def test_spawned_child_does_not_inherit_owner() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    child_entered = asyncio.Event()

    async with gate.operation("parent"):
        async with gate.operation("nested"):
            assert gate.snapshot()["active_operation"] == "parent"
        child = asyncio.create_task(enter_and_signal(gate, "child", child_entered))
        await wait_for_queue_depth(gate, 1)
        assert not child_entered.is_set()

    await child
    assert child_entered.is_set()


@pytest.mark.asyncio
async def test_expired_waiter_never_enters_body() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=0.01)
    entered = False

    async def blocked() -> None:
        nonlocal entered
        async with gate.operation("blocked"):
            entered = True

    async with gate.operation("owner"):
        blocked_task = asyncio.create_task(blocked())
        await wait_for_queue_depth(gate, 1)
        with pytest.raises(SessionBusyTimeoutError, match=r"one.*blocked"):
            await blocked_task
    assert entered is False
    assert gate.snapshot()["queue_depth"] == 0


@pytest.mark.asyncio
async def test_triple_reentry_releases_once_at_outermost_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    log_cap = _LogCapture()
    monkeypatch.setattr(operation_gate, "log", log_cap)
    active_duration_cap = _MetricCapture()
    monkeypatch.setattr(operation_gate, "_ACTIVE_DURATION", active_duration_cap)
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)

    async with gate.operation("outer"):
        assert gate.snapshot()["active_operation"] == "outer"
        async with gate.operation("middle"):
            async with gate.operation("inner"):
                assert gate.snapshot()["active_operation"] == "outer"
            assert gate.snapshot()["active_operation"] == "outer"
        assert gate.snapshot()["active_operation"] == "outer"

    assert gate.snapshot()["active_operation"] is None
    assert len(active_duration_cap.calls) == 1
    released = [event for event in log_cap.events if event[0] == "octowright.operation.released"]
    assert len(released) == 1
    assert released[0][1]["operation"] == "outer"


@pytest.mark.asyncio
async def test_cross_gate_operations_run_concurrently() -> None:
    gate_a = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    gate_b = SessionOperationGate("two", "firefox", queue_timeout_seconds=30)
    a_entered = asyncio.Event()
    b_entered = asyncio.Event()
    release_a = asyncio.Event()

    async def hold_a() -> None:
        async with gate_a.operation("hold"):
            a_entered.set()
            await release_a.wait()

    async def use_b() -> None:
        async with gate_b.operation("use"):
            b_entered.set()

    a_task = asyncio.create_task(hold_a())
    await a_entered.wait()
    b_task = asyncio.create_task(use_b())
    async with asyncio.timeout(1):
        await b_entered.wait()
    assert gate_a.snapshot()["active_operation"] == "hold"
    release_a.set()
    await asyncio.gather(a_task, b_task)


@pytest.mark.asyncio
async def test_queued_waiter_cancellation_leaves_no_trace() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    entered = False

    async def blocked() -> None:
        nonlocal entered
        async with gate.operation("blocked"):
            entered = True

    async with gate.operation("owner"):
        blocked_task = asyncio.create_task(blocked())
        await wait_for_queue_depth(gate, 1)
        blocked_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked_task
    assert entered is False
    assert gate.snapshot()["queue_depth"] == 0

    async with gate.operation("after"):
        assert gate.snapshot()["active_operation"] == "after"


@pytest.mark.asyncio
async def test_active_operation_cancellation_still_releases_gate() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    owner_entered = asyncio.Event()
    never = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("owner"):
            owner_entered.set()
            await never.wait()

    owner_task = asyncio.create_task(owner())
    await owner_entered.wait()
    owner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner_task

    assert gate.snapshot()["active_operation"] is None
    async with gate.operation("after"):
        assert gate.snapshot()["active_operation"] == "after"


@pytest.mark.asyncio
async def test_arbitrary_exception_releases_gate_without_breaking_state() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)

    with pytest.raises(ValueError, match="boom"):
        async with gate.operation("failing"):
            raise ValueError("boom")

    snap = gate.snapshot()
    assert snap["state"] == "open"
    assert snap["active_operation"] is None
    async with gate.operation("after"):
        assert gate.snapshot()["active_operation"] == "after"


@pytest.mark.asyncio
async def test_unsafe_operation_name_rejected_before_any_side_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    log_cap = _LogCapture()
    monkeypatch.setattr(operation_gate, "log", log_cap)
    rejected_cap = _MetricCapture()
    monkeypatch.setattr(operation_gate, "_REJECTED", rejected_cap)
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    unsafe = "https://secret.test/login?token=abc123"

    with pytest.raises(ValueError, match="fixed identifier"):
        async with gate.operation(unsafe):
            raise AssertionError("body must not run for a rejected name")

    assert gate.snapshot()["queue_depth"] == 0
    assert log_cap.events == []
    assert rejected_cap.calls == []


@pytest.mark.asyncio
async def test_structured_logs_use_bounded_fields_only(monkeypatch: pytest.MonkeyPatch) -> None:
    log_cap = _LogCapture()
    monkeypatch.setattr(operation_gate, "log", log_cap)
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("owner"):
            owner_entered.set()
            await release_owner.wait()

    async def waiter() -> None:
        async with gate.operation("waiter"):
            pass

    owner_task = asyncio.create_task(owner())
    await owner_entered.wait()
    waiter_task = asyncio.create_task(waiter())
    await wait_for_queue_depth(gate, 1)
    release_owner.set()
    await asyncio.gather(owner_task, waiter_task)

    names = {event for event, _fields in log_cap.events}
    assert "octowright.operation.queued" in names
    assert "octowright.operation.admitted" in names
    assert "octowright.operation.released" in names

    allowed_keys = {
        "instance_id",
        "kind",
        "operation",
        "state",
        "queue_depth",
        "queue_wait_ms",
        "active_duration_ms",
        "outcome",
        "reason",
    }
    for event, fields in log_cap.events:
        assert set(fields) <= allowed_keys, (event, fields)
        assert fields.get("operation") in {"owner", "waiter"}
        assert "task" not in fields
        assert "selector" not in fields
        assert "url" not in fields


@pytest.mark.asyncio
async def test_timeout_records_warning_log(monkeypatch: pytest.MonkeyPatch) -> None:
    log_cap = _LogCapture()
    monkeypatch.setattr(operation_gate, "log", log_cap)
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=0.01)

    async def blocked() -> None:
        async with gate.operation("blocked"):
            pass

    async with gate.operation("owner"):
        blocked_task = asyncio.create_task(blocked())
        await wait_for_queue_depth(gate, 1)
        with pytest.raises(SessionBusyTimeoutError):
            await blocked_task

    timeouts = [event for event in log_cap.events if event[0] == "octowright.operation.timeout"]
    assert len(timeouts) == 1
    assert timeouts[0][1]["operation"] == "blocked"


@pytest.mark.asyncio
async def test_grant_that_races_a_timeout_cleanup_is_rolled_back() -> None:
    """Directly exercises ``_cleanup_waiter``'s "granted after all" branch.

    Reproducing the real race (a FIFO grant landing in the same event-loop
    tick as a wait_for timeout or an external cancel) via wall-clock timing
    would be flaky; the scheduling interleaving that triggers it is
    deterministic in principle but not practical to pin down through the
    public API alone, so this test drives the internal state directly to
    prove the rollback path leaves the gate consistent.
    """
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    task = asyncio.current_task()
    assert task is not None
    waiter = operation_gate._Waiter(task, "raced", gate._clock(), asyncio.get_running_loop().create_future())
    gate._waiters.append(waiter)
    gate._queue_depth_delta(+1)
    gate._grant_next_locked()
    assert waiter.granted is True
    assert gate.snapshot()["active_operation"] == "raced"

    await gate._remove_or_release_waiter(waiter, "cancelled")

    snap = gate.snapshot()
    assert snap["active_operation"] is None
    assert snap["queue_depth"] == 0
    assert gate._owner_task is None

    async with gate.operation("after"):
        assert gate.snapshot()["active_operation"] == "after"


@pytest.mark.asyncio
async def test_release_by_non_owner_breaks_only_this_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    log_cap = _LogCapture()
    monkeypatch.setattr(operation_gate, "log", log_cap)
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    other = SessionOperationGate("two", "firefox", queue_timeout_seconds=30)

    await gate._acquire("owner", USE_DEFAULT)
    foreign_task = asyncio.create_task(asyncio.sleep(0))
    try:
        bogus_lease = operation_gate._LeaseToken(foreign_task, "owner")
        with pytest.raises(OperationGateInvariantError):
            await gate._release(bogus_lease, "ok")

        assert gate.snapshot()["state"] == "broken"

        broken_events = [event for event in log_cap.events if event[0] == "octowright.operation.invariant_broken"]
        assert len(broken_events) == 1
        fields = broken_events[0][1]
        assert fields["instance_id"] == "one"
        assert fields["kind"] == "chromium"
        assert fields["state"] == "broken"
        assert fields["reason"] == "operation released by a task that does not own the gate"

        async def try_later() -> None:
            async with gate.operation("later"):
                raise AssertionError("later work must not enter a broken gate")

        later_task = asyncio.create_task(try_later())
        with pytest.raises(OperationGateInvariantError):
            await later_task

        async with other.operation("healthy"):
            assert other.snapshot()["active_operation"] == "healthy"
        assert other.snapshot()["state"] == "open"
    finally:
        await foreign_task


@pytest.mark.asyncio
async def test_snapshot_derives_active_and_wait_ms_from_injected_clock() -> None:
    clock_value = 100.0

    def fake_clock() -> float:
        return clock_value

    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30, clock=fake_clock)
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("owner"):
            owner_entered.set()
            await release_owner.wait()

    async def waiter() -> None:
        async with gate.operation("waiter"):
            pass

    owner_task = asyncio.create_task(owner())
    await owner_entered.wait()

    clock_value = 102.0
    waiter_task = asyncio.create_task(waiter())
    await wait_for_queue_depth(gate, 1)

    clock_value = 105.5
    snap = gate.snapshot()
    assert snap["active_for_ms"] == 5500
    assert snap["oldest_wait_ms"] == 3500

    release_owner.set()
    await asyncio.gather(owner_task, waiter_task)

    idle = gate.snapshot()
    assert idle["active_for_ms"] is None
    assert idle["oldest_wait_ms"] is None


@pytest.mark.asyncio
async def test_release_survives_repeated_cancellation_of_the_owner() -> None:
    """``asyncio.Task.cancel()`` can be requested more than once before the
    task processes any of them. ``_join_after_cancellation`` must absorb
    every extra cancel via ``current.uncancel()`` and keep joining the
    detached release task rather than letting a second cancel escape
    mid-cleanup and leave the gate with a dangling owner.
    """
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    entered = asyncio.Event()
    never = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("owner"):
            entered.set()
            await never.wait()

    owner_task = asyncio.create_task(owner())
    await entered.wait()

    await gate._admission_lock.acquire()
    owner_task.cancel()
    await asyncio.sleep(0)
    owner_task.cancel()
    await asyncio.sleep(0)
    assert not owner_task.done()
    gate._admission_lock.release()

    with pytest.raises(asyncio.CancelledError):
        await owner_task

    assert gate.snapshot()["active_operation"] is None
    async with gate.operation("after"):
        assert gate.snapshot()["active_operation"] == "after"


@pytest.mark.asyncio
async def test_arbitrary_baseexception_during_wait_cleans_up_queued_waiter() -> None:
    class _InjectedBaseException(BaseException):
        pass

    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    owner_entered = asyncio.Event()
    never = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("owner"):
            owner_entered.set()
            await never.wait()

    async def blocked() -> None:
        async with gate.operation("blocked"):
            pass

    owner_task = asyncio.create_task(owner())
    await owner_entered.wait()
    blocked_task = asyncio.create_task(blocked())
    await wait_for_queue_depth(gate, 1)

    queued_waiter = gate._waiters[0]
    queued_waiter.ready.set_exception(_InjectedBaseException())

    with pytest.raises(_InjectedBaseException):
        await blocked_task

    assert gate.snapshot()["queue_depth"] == 0
    assert queued_waiter not in gate._waiters

    owner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner_task

    async with gate.operation("after"):
        assert gate.snapshot()["active_operation"] == "after"
