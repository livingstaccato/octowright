# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal, LiteralString, TypeVar

# Private module: import SessionOperationGate from operation_gate.py, never
# import this module directly first. operation_gate.py imports _CloseGateMixin
# from here after defining the names below, so operation_gate.py must load
# first -- importing this module on its own triggers operation_gate.py to
# load reentrantly and fails with ImportError on a not-yet-bound name.
from octowright.session.operation_gate import (
    CloseReservation,
    OperationGateInvariantError,
    OperationGateState,
    SessionClosedError,
    _LeaseToken,
    _observe_future_exception,
    _run_shielded,
    _Waiter,
    validate_operation_name,
)

_T = TypeVar("_T")


class _CloseGateMixin:
    """Close reservation, external-close, and control-plane mutation.

    Split out of ``operation_gate.py`` to keep that module under the
    repository's LOC ceiling; every method here only ever runs as part of a
    composed ``SessionOperationGate`` instance. The field/stub-method
    declarations below (never assigned here) describe that host's real
    attributes and core scheduler methods so mypy can check this mixin in
    isolation, without a nominal (and circular) import of the concrete class
    itself. Kept in sync by hand -- there is no runtime enforcement.
    """

    _admission_lock: asyncio.Lock
    _close_reservation: CloseReservation | None
    _granted_close_reservation: CloseReservation | None
    _state: OperationGateState
    _waiters: deque[_Waiter]
    _owner_task: asyncio.Task[object] | None
    _root_operation: str | None
    _active_since: float | None
    _depth: int
    kind: str
    instance_id: str
    _clock: Callable[[], float]

    @staticmethod
    def _current_task() -> asyncio.Task[object]:
        raise NotImplementedError

    def _raise_if_not_open(self, name: str) -> None: ...
    def _fail_queued_locked(self, error_cls: type[RuntimeError], *, reason: str) -> None: ...
    def _break_locked(self, reason: str) -> None: ...
    def _grant_next_locked(self) -> None: ...
    def _publish_diagnostics_locked(self) -> None: ...
    def _queue_depth_delta(self, delta: int) -> None: ...
    def _record_active_duration_locked(self, outcome: Literal["ok", "error", "cancelled"]) -> None: ...

    def _rejection_message(self, name: str, state: OperationGateState) -> str:
        raise NotImplementedError

    def _invariant_message(self) -> str:
        raise NotImplementedError

    async def control_update(
        self,
        operation_name: LiteralString,
        mutator: Callable[[], _T],
    ) -> _T:
        validate_operation_name(operation_name)
        async with self._admission_lock:
            return mutator()

    def _new_close_reservation(self, name: str, *, ready: bool, teardown_only: bool) -> CloseReservation:
        outcome: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        _observe_future_exception(outcome)
        ready_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        if ready:
            ready_future.set_result(None)
        waiter = _Waiter(None, name, self._clock(), ready_future)
        return CloseReservation(operation_name=name, waiter=waiter, outcome=outcome, teardown_only=teardown_only)

    async def reserve_close(
        self,
        operation_name: LiteralString,
        *,
        preflight: Callable[[], None],
    ) -> CloseReservation:
        name = validate_operation_name(operation_name)
        async with self._admission_lock:
            if self._close_reservation is not None:
                return self._close_reservation
            preflight()
            if self._state is OperationGateState.BROKEN:
                reservation = self._new_close_reservation(name, ready=True, teardown_only=True)
                self._close_reservation = reservation
                return reservation
            self._raise_if_not_open(name)
            reservation = self._new_close_reservation(name, ready=False, teardown_only=False)
            self._close_reservation = reservation
            self._state = OperationGateState.CLOSING
            self._waiters.append(reservation.waiter)
            self._queue_depth_delta(+1)
            self._publish_diagnostics_locked()
            self._grant_next_locked()
            return reservation

    @asynccontextmanager
    async def close_operation(self, reservation: CloseReservation) -> AsyncIterator[None]:
        if reservation.teardown_only:
            yield
            return
        await asyncio.shield(reservation.waiter.ready)
        task = self._current_task()
        async with self._admission_lock:
            if self._granted_close_reservation is not reservation:
                raise OperationGateInvariantError(self._invariant_message())
            if self._state is OperationGateState.CLOSED:
                # mark_closed_external() landed in the grant-to-bind gap: the
                # ticket's admission future already resolved before the
                # browser closed out from under it. There is nothing left to
                # prepare -- clear the sentinel and route the coordinator to
                # teardown-only cleanup instead of a page/context touch that
                # would only surface Playwright's own closed error.
                self._granted_close_reservation = None
                self._root_operation = None
                self._active_since = None
                self._publish_diagnostics_locked()
                raise SessionClosedError(self._rejection_message(reservation.operation_name, self._state))
            self._granted_close_reservation = None
            self._owner_task = task
            self._root_operation = reservation.operation_name
            self._active_since = self._clock()
            self._depth = 1
            self._publish_diagnostics_locked()
        outcome: Literal["ok", "error", "cancelled"] = "ok"
        try:
            yield
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except BaseException:
            outcome = "error"
            raise
        finally:
            lease = _LeaseToken(task, reservation.operation_name)
            await _run_shielded(self._release_close(lease, outcome))

    async def _release_close(self, lease: _LeaseToken, outcome: Literal["ok", "error", "cancelled"]) -> None:
        async with self._admission_lock:
            if self._owner_task is not lease.owner_task:
                self._break_locked("close operation released by a task that does not own the gate")
                self._publish_diagnostics_locked()
                raise OperationGateInvariantError(self._invariant_message())
            self._depth -= 1
            if self._depth:
                return
            self._record_active_duration_locked(outcome)
            self._owner_task = None
            self._root_operation = None
            self._active_since = None
            self._publish_diagnostics_locked()

    def mark_closed_external(self) -> None:
        if self._state is OperationGateState.CLOSED:
            return
        self._state = OperationGateState.CLOSED
        self._fail_queued_locked(SessionClosedError, reason="external_close")
        self._publish_diagnostics_locked()

    def reserve_external_teardown(self, operation_name: LiteralString) -> CloseReservation:
        name = validate_operation_name(operation_name)
        if self._state is not OperationGateState.CLOSED:
            raise OperationGateInvariantError(f"reserve_external_teardown called before mark_closed_external ({name})")
        if self._close_reservation is not None:
            return self._close_reservation
        reservation = self._new_close_reservation(name, ready=True, teardown_only=True)
        self._close_reservation = reservation
        return reservation

    def complete_close(self, reservation: CloseReservation, result: object) -> None:
        self._state = OperationGateState.CLOSED
        self._fail_queued_locked(SessionClosedError, reason="session_closed")
        if not reservation.outcome.done():
            reservation.outcome.set_result(result)

    def fail_close(self, reservation: CloseReservation, exc: BaseException) -> None:
        self._state = OperationGateState.CLOSED
        self._fail_queued_locked(SessionClosedError, reason="session_closed")
        if not reservation.outcome.done():
            reservation.outcome.set_exception(exc)
