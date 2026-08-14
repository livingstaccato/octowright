# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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


async def wait_for_active(gate: SessionOperationGate, name: str) -> None:
    async with asyncio.timeout(1):
        while gate.snapshot()["active_operation"] != name:
            await asyncio.sleep(0)


async def enter_and_signal(gate: SessionOperationGate, name: str, entered: asyncio.Event) -> None:
    async with gate.operation(name):
        entered.set()


async def enter_once(gate: SessionOperationGate, name: str) -> None:
    async with gate.operation(name):
        pass


async def run_recorded(gate: SessionOperationGate, name: str, sequence: list[str]) -> None:
    async with gate.operation(name):
        sequence.append(name)


async def run_close_reservation(
    gate: SessionOperationGate,
    reservation: operation_gate.CloseReservation,
    sequence: list[str],
) -> None:
    async with gate.close_operation(reservation):
        sequence.append("close")
    gate.complete_close(reservation, None)


@asynccontextmanager
async def hold_gate(gate: SessionOperationGate, name: str, release: asyncio.Event) -> AsyncIterator[None]:
    async with gate.operation(name):
        yield
        await release.wait()


class ProtectedForTest(RuntimeError):
    pass


def raise_protected(protected: bool) -> None:
    if protected:
        raise ProtectedForTest("browser is protected")


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


@pytest.mark.asyncio
async def test_close_cutoff_drains_earlier_waiters_and_rejects_later_work() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    release_owner = asyncio.Event()
    sequence: list[str] = []

    async def owner() -> None:
        async with gate.operation("owner"):
            await release_owner.wait()
            sequence.append("owner")

    owner_task = asyncio.create_task(owner())
    await wait_for_active(gate, "owner")
    earlier = asyncio.create_task(run_recorded(gate, "earlier", sequence))
    await wait_for_queue_depth(gate, 1)
    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    assert gate.snapshot()["state"] == "closing"
    with pytest.raises(SessionClosingError):
        async with gate.operation("later"):
            raise AssertionError("later work must not enter")

    close_task = asyncio.create_task(run_close_reservation(gate, reservation, sequence))
    release_owner.set()
    await asyncio.gather(owner_task, earlier, close_task)
    assert sequence == ["owner", "earlier", "close"]
    assert gate.snapshot()["state"] == "closed"


@pytest.mark.asyncio
async def test_external_close_fails_waiters_but_not_another_gate() -> None:
    first = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    second = SessionOperationGate("two", "firefox", queue_timeout_seconds=30)
    release = asyncio.Event()
    async with hold_gate(first, "owner", release):
        waiter = asyncio.create_task(enter_once(first, "queued"))
        await wait_for_queue_depth(first, 1)
        first.mark_closed_external()
        with pytest.raises(SessionClosedError):
            await waiter
        async with second.operation("healthy"):
            assert second.snapshot()["active_operation"] == "healthy"
        release.set()


@pytest.mark.asyncio
async def test_control_update_and_close_preflight_have_one_winner() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    protected = False

    def protect() -> None:
        nonlocal protected
        protected = True

    await gate.control_update("browser_set_protected", protect)
    with pytest.raises(ProtectedForTest):
        await gate.reserve_close("browser_close", preflight=lambda: raise_protected(protected))
    assert gate.snapshot()["state"] == "open"


@pytest.mark.asyncio
async def test_duplicate_reserve_close_returns_retained_object() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    calls = 0

    def preflight() -> None:
        nonlocal calls
        calls += 1

    first = await gate.reserve_close("browser_close", preflight=preflight)
    second = await gate.reserve_close("browser_close", preflight=preflight)
    assert first is second
    assert calls == 1

    async with gate.close_operation(first):
        pass
    gate.complete_close(first, "done")
    assert await second.wait() == "done"


@pytest.mark.asyncio
async def test_cancelled_close_waiter_does_not_cancel_shared_outcome() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)

    waiting = asyncio.create_task(reservation.wait())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not reservation.outcome.cancelled()
    assert not reservation.outcome.done()

    async with gate.close_operation(reservation):
        pass
    boom = RuntimeError("boom")
    gate.fail_close(reservation, boom)

    assert reservation.outcome.exception() is boom
    with pytest.raises(RuntimeError) as excinfo:
        await reservation.wait()
    assert excinfo.value is boom


@pytest.mark.asyncio
async def test_external_close_invalidates_pending_close_ticket_and_teardown_reuses_it() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    release_owner = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("owner"):
            await release_owner.wait()

    owner_task = asyncio.create_task(owner())
    await wait_for_active(gate, "owner")

    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    assert gate.snapshot()["state"] == "closing"
    assert not reservation.waiter.ready.done()

    gate.mark_closed_external()
    assert gate.snapshot()["state"] == "closed"
    with pytest.raises(SessionClosedError):
        await reservation.waiter.ready
    assert not reservation.outcome.done()

    teardown = gate.reserve_external_teardown("session_external_teardown")
    assert teardown is reservation
    assert teardown.teardown_only is True

    gate.complete_close(reservation, None)
    assert await reservation.wait() is None

    release_owner.set()
    await owner_task


@pytest.mark.asyncio
async def test_external_close_after_grant_but_before_bind_routes_to_teardown() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    assert gate.snapshot()["state"] == "closing"
    assert gate._granted_close_reservation is reservation
    assert reservation.waiter.ready.done()

    gate.mark_closed_external()
    assert gate.snapshot()["state"] == "closed"
    assert gate._granted_close_reservation is reservation

    with pytest.raises(SessionClosedError):
        async with gate.close_operation(reservation):
            raise AssertionError("closed reservation must not enter close_operation body")

    assert gate._granted_close_reservation is None
    assert not reservation.outcome.done()


@pytest.mark.asyncio
async def test_reserve_external_teardown_is_idempotent_after_external_close() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)

    with pytest.raises(OperationGateInvariantError):
        gate.reserve_external_teardown("session_external_teardown")

    gate.mark_closed_external()
    first = gate.reserve_external_teardown("session_external_teardown")
    second = gate.reserve_external_teardown("session_external_teardown")
    assert first is second
    assert first.teardown_only is True
    assert not first.outcome.done()

    gate.complete_close(first, None)
    assert await first.wait() is None


@pytest.mark.asyncio
async def test_broken_gate_close_reservation_is_teardown_only_but_runs_preflight() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)

    await gate._acquire("owner", USE_DEFAULT)
    foreign_task = asyncio.create_task(asyncio.sleep(0))
    try:
        bogus_lease = operation_gate._LeaseToken(foreign_task, "owner")
        with pytest.raises(OperationGateInvariantError):
            await gate._release(bogus_lease, "ok")
        assert gate.snapshot()["state"] == "broken"

        preflight_calls = 0

        def preflight() -> None:
            nonlocal preflight_calls
            preflight_calls += 1

        reservation = await gate.reserve_close("browser_close", preflight=preflight)
        assert preflight_calls == 1
        assert reservation.teardown_only is True

        async with gate.close_operation(reservation):
            pass

        gate.complete_close(reservation, None)
        assert gate.snapshot()["state"] == "closed"
    finally:
        await foreign_task


@pytest.mark.asyncio
async def test_break_locked_fails_queued_waiters_with_invariant_error() -> None:
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    release_owner = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("owner"):
            await release_owner.wait()

    owner_task = asyncio.create_task(owner())
    await wait_for_active(gate, "owner")
    queued = asyncio.create_task(enter_once(gate, "queued"))
    await wait_for_queue_depth(gate, 1)

    foreign_task = asyncio.create_task(asyncio.sleep(0))
    try:
        bogus_lease = operation_gate._LeaseToken(foreign_task, "owner")
        with pytest.raises(OperationGateInvariantError):
            await gate._release(bogus_lease, "ok")

        with pytest.raises(OperationGateInvariantError):
            await queued
        assert gate.snapshot()["queue_depth"] == 0
        assert gate.snapshot()["state"] == "broken"
    finally:
        await foreign_task
        release_owner.set()
        await owner_task


@pytest.mark.asyncio
async def test_cancelled_close_coordinator_does_not_wedge_the_gate() -> None:
    """Regression test for the CRITICAL review finding: cancelling the close
    coordinator inside ``close_operation`` must not leave the gate in a
    ``closing`` limbo that (a) makes a retry raise a false "broken" invariant
    error and (b) leaves duplicate ``reservation.wait()`` callers hanging
    forever. The gate must become explicitly, terminally ``closed`` instead.
    """
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    entered = asyncio.Event()
    never = asyncio.Event()

    async def coordinator() -> None:
        reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
        async with gate.close_operation(reservation):
            entered.set()
            await never.wait()

    coordinator_task = asyncio.create_task(coordinator())
    await entered.wait()
    coordinator_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await coordinator_task

    assert gate.snapshot()["state"] == "closed"

    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    with pytest.raises(SessionClosedError):
        await asyncio.wait_for(reservation.wait(), timeout=1)

    with pytest.raises(SessionClosedError):
        async with gate.close_operation(reservation):
            raise AssertionError("a failed close ticket must not be re-entered")


@pytest.mark.asyncio
async def test_double_close_operation_after_normal_completion_raises_session_closed_error() -> None:
    """Regression test for IMPORTANT #3: a second ``close_operation`` call on
    an already-completed reservation must raise ``SessionClosedError`` (the
    gate is simply closed), not ``OperationGateInvariantError`` claiming the
    gate is broken.
    """
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    async with gate.close_operation(reservation):
        pass
    gate.complete_close(reservation, None)

    with pytest.raises(SessionClosedError):
        async with gate.close_operation(reservation):
            raise AssertionError("a completed close ticket must not be re-entered")


@pytest.mark.asyncio
async def test_close_operation_enters_body_for_reserve_external_teardown_result() -> None:
    """Regression test for IMPORTANT #2: a fresh ``reserve_external_teardown``
    reservation (no prior ``reserve_close``) must let ``close_operation``
    take its bare-yield teardown path.
    """
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    gate.mark_closed_external()
    reservation = gate.reserve_external_teardown("session_external_teardown")

    entered = False
    async with gate.close_operation(reservation):
        entered = True
    assert entered is True


@pytest.mark.asyncio
async def test_close_operation_enters_body_for_teardown_of_invalidated_fifo_reservation() -> None:
    """Regression test for IMPORTANT #2: the retained reservation from an
    earlier ``reserve_close`` that external close invalidated must ALSO let
    ``close_operation`` take its bare-yield teardown path once handed back
    through ``reserve_external_teardown`` -- Task 7's cleanup coordinator
    needs one pattern (``close_operation(reserve_external_teardown(...))``)
    that works for both the fresh and the FIFO-origin case.
    """
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    release_owner = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("owner"):
            await release_owner.wait()

    owner_task = asyncio.create_task(owner())
    await wait_for_active(gate, "owner")

    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    gate.mark_closed_external()

    teardown = gate.reserve_external_teardown("session_external_teardown")
    assert teardown is reservation
    assert teardown.teardown_only is True

    entered = False
    async with gate.close_operation(teardown):
        entered = True
    assert entered is True

    release_owner.set()
    await owner_task


@pytest.mark.asyncio
async def test_unobserved_close_outcome_exception_does_not_reach_loop_handler() -> None:
    """Regression test for IMPORTANT #5: a failed close outcome that nobody
    ever inspects must not reach the event loop's exception handler when
    garbage collected -- proving ``_observe_future_exception`` actually
    suppresses it, not merely that a later ``.exception()`` call (which
    itself would retrieve it) sees the same object.
    """
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30)
    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    async with gate.close_operation(reservation):
        pass

    loop = asyncio.get_running_loop()
    handled: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: handled.append(context))
    try:
        gate.fail_close(reservation, RuntimeError("boom"))
        outcome = reservation.outcome
        del reservation
        await asyncio.sleep(0)
        del outcome
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert handled == []
