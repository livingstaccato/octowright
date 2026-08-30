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

from octowright.session.operation_gate_types import (
    CloseReservation,
    OperationGateInvariantError,
    OperationGateState,
    SessionCloseAbortedError,
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
        name = validate_operation_name(operation_name)
        async with self._admission_lock:
            # A control-plane mutation is still a mutation of session state, so
            # it must lose to a close that already committed: once
            # ``reserve_close`` has moved the gate to CLOSING (or it is CLOSED /
            # BROKEN), the caller gets the same terminal error an ordinary
            # operation would, instead of silently mutating a dying session.
            self._raise_if_not_open(name)
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
            if self._state is OperationGateState.CLOSED:
                # Either an entirely ordinary double-close (this exact
                # reservation already completed/failed) or mark_closed_external()
                # landed in the grant-to-bind gap (ticket already granted
                # before the browser closed out from under it) -- either way
                # there is nothing left to prepare. Checked before the
                # sentinel-identity check below so a plain double-close gets
                # SessionClosedError, not a misleading "broken" error (the
                # gate isn't broken, it's just closed).
                if self._granted_close_reservation is reservation:
                    self._granted_close_reservation = None
                    self._root_operation = None
                    self._active_since = None
                    self._publish_diagnostics_locked()
                raise SessionClosedError(self._rejection_message(reservation.operation_name, self._state))
            if self._granted_close_reservation is not reservation:
                raise OperationGateInvariantError(
                    f"session {self.instance_id!r} close_operation called for a reservation that is "
                    f"not the currently granted close ticket (kind={self.kind!r})"
                )
            self._granted_close_reservation = None
            self._owner_task = task
            self._root_operation = reservation.operation_name
            self._active_since = self._clock()
            self._depth = 1
            self._publish_diagnostics_locked()
        outcome: Literal["ok", "error", "cancelled"] = "ok"
        exc: BaseException | None = None
        try:
            yield
        except asyncio.CancelledError as caught:
            outcome = "cancelled"
            exc = caught
            raise
        except BaseException as caught:
            outcome = "error"
            exc = caught
            raise
        finally:
            lease = _LeaseToken(task, reservation.operation_name)
            await _run_shielded(self._release_close(lease, outcome, reservation, exc))

    async def _release_close(
        self,
        lease: _LeaseToken,
        outcome: Literal["ok", "error", "cancelled"],
        reservation: CloseReservation,
        exc: BaseException | None,
    ) -> None:
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
            if outcome == "ok":
                self._publish_diagnostics_locked()
                return
            # The close body was cancelled or raised mid-close: the browser's
            # real state is now unknown, so this ticket is a failed close, not
            # a resumable one -- reopening to `open` would let ordinary work
            # resume against a possibly half-closed browser. Resolve it the
            # same terminal way an in-band close finishes (state closed,
            # shared outcome set) so no `reservation.wait()` caller hangs
            # forever and a retry lands on the `state is CLOSED` branch above
            # instead of a phantom-owner invariant error. Hand `fail_close`
            # the RAW `exc` rather than converting a `CancelledError` here --
            # `_terminal_close_failure` (below) is the SOLE place that
            # normalizes one, so a cancellation reaching this branch
            # directly (propagating cleanly through `close_operation`'s
            # body) produces the exact same `SessionCloseAbortedError` as
            # one a teardown helper swallowed and returned instead of
            # raising, rather than a plain `SessionClosedError` for what is
            # the same underlying cause with two different landing spots.
            # `exc is None` (outcome != "ok" with nothing caught -- not
            # reachable via `close_operation` today, but defensive) still
            # needs a synthesized failure, since there is nothing to hand
            # `fail_close` otherwise.
            failure: BaseException = (
                SessionClosedError(
                    f"session {self.instance_id!r} close operation {reservation.operation_name!r} was "
                    f"interrupted before completing; the session is now closed (kind={self.kind!r})"
                )
                if exc is None
                else exc
            )
            self.fail_close(reservation, failure)

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
            reservation = self._close_reservation
            # A retained reservation from reserve_close() is FIFO-shaped
            # (teardown_only=False) by construction. The caller here always
            # wants the bare-yield teardown path from close_operation --
            # CloseReservation is a mutable slots dataclass, so flip the flag
            # in place rather than making the caller juggle two reservation
            # shapes for what is conceptually one cleanup handle.
            reservation.teardown_only = True
            if self._granted_close_reservation is reservation:
                self._granted_close_reservation = None
                self._root_operation = None
                self._active_since = None
                self._publish_diagnostics_locked()
            return reservation
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
            reservation.outcome.set_exception(self._terminal_close_failure(reservation, exc))

    def _terminal_close_failure(self, reservation: CloseReservation, exc: BaseException) -> BaseException:
        """Never let a raw ``CancelledError`` reach a ``reservation.wait()`` caller.

        The SOLE normalizer -- ``_release_close`` deliberately hands this
        its raw ``exc`` rather than converting a cancellation itself, so a
        ``CancelledError`` reaching ``fail_close`` produces the exact same
        ``SessionCloseAbortedError`` regardless of WHERE it landed: swallowed
        and returned by a teardown helper (``close_helpers.prepare_then_
        teardown`` / ``core_ops_standalone_close._run_standalone_teardown``
        both do this by design, since they must always attempt the teardown
        even when a preceding step failed or was cancelled -- ``close_
        operation`` then never sees an exception at all, ``outcome`` stays
        ``"ok"``, and ``_release_close`` returns before ever calling
        ``fail_close``, so the raw ``CancelledError`` reaches here from
        ``_coordinate_close``'s own ``finally`` instead), OR propagating
        directly through ``close_operation``'s body with nothing in between
        to swallow it (``_release_close``'s ``outcome != "ok"`` branch,
        reachable via a direct cancellation of the coordinator task --
        see ``tests/session/test_operation_gate.py::
        test_cancelled_close_coordinator_does_not_wedge_the_gate`` for the
        gate-level shape of it). Before this was the sole choke point, the
        two landing spots built two DIFFERENT types for the same underlying
        cause -- a plain ``SessionClosedError`` from ``_release_close``,
        this ``SessionCloseAbortedError`` from here -- so a caller like
        ``relaunch._close_with_fallback_snapshot`` that discriminates on
        type would treat one as an ordinary safe race and the other as the
        loud failure it actually is, purely depending on cancellation
        timing. A ``CancelledError`` is a ``BaseException``, not caught by
        an MCP tool handler's ``except Exception``, and marks the AWAITING
        task cancelled too if it escapes uncaught -- exactly the
        "session-scoped problem looks like something bigger" failure this
        whole plan exists to prevent. This is the single choke point every
        ``fail_close`` caller passes through (the pool coordinator, the
        standalone-session coordinator, and ``_release_close`` itself), so
        normalizing here -- and ONLY here -- catches every landing spot
        without duplicating (and risking diverging) the check anywhere else.
        """
        if isinstance(exc, asyncio.CancelledError):
            return SessionCloseAbortedError(
                f"session {self.instance_id!r} close operation {reservation.operation_name!r} was "
                f"aborted mid-teardown (cancelled) before completing; the browser's actual close "
                f"state is unconfirmed (kind={self.kind!r})"
            )
        return exc
