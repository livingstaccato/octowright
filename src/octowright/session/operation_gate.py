# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import math
import os
import re
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Literal, LiteralString, TypedDict

from provide.telemetry import get_logger

from octowright._tracing import counter, gauge, histogram

DEFAULT_OPERATION_QUEUE_TIMEOUT_SECONDS = 300.0
_OPERATION_TIMEOUT_ENV = "OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS"
_OPERATION_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class OperationGateState(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    BROKEN = "broken"


class SessionBusyTimeoutError(RuntimeError):
    """The operation's FIFO ticket expired before it owned the session."""


class SessionClosingError(RuntimeError):
    """The operation arrived after the session close cutoff."""


class SessionClosedError(RuntimeError):
    """The underlying browser session is already closed."""


class OperationGateInvariantError(RuntimeError):
    """The gate's ownership/state invariants were violated."""


class OperationGateSnapshot(TypedDict):
    state: Literal["open", "closing", "closed", "broken"]
    active_operation: str | None
    active_for_ms: int | None
    queue_depth: int
    oldest_wait_ms: int | None
    queue_timeout_seconds: float


def _positive_finite_seconds(value: object, *, source: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} must be positive finite seconds, got {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{source} must be positive finite seconds, got {value!r}")
    return parsed


def resolve_operation_queue_timeout_seconds(
    explicit: float | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> float:
    if explicit is not None:
        return _positive_finite_seconds(explicit, source="operation_queue_timeout_seconds")
    source = os.environ if environ is None else environ
    raw = source.get(_OPERATION_TIMEOUT_ENV, str(DEFAULT_OPERATION_QUEUE_TIMEOUT_SECONDS))
    return _positive_finite_seconds(raw, source=_OPERATION_TIMEOUT_ENV)


def validate_operation_name(name: str) -> str:
    if not _OPERATION_NAME_RE.fullmatch(name):
        raise ValueError(f"operation name must be a fixed identifier, got {name!r}")
    return name


_QUEUE_WAIT = histogram("octowright_operation_queue_wait_seconds", unit="s")
_ACTIVE_DURATION = histogram("octowright_operation_active_duration_seconds", unit="s")
_QUEUE_TIMEOUT = counter("octowright_operation_queue_timeout_total")
_REJECTED = counter("octowright_operation_rejected_total")
_QUEUE_DEPTH = gauge("octowright_operation_queue_depth", unit="1")

log = get_logger(__name__)


class UseDefault(Enum):
    """Sentinel distinguishing the gate's configured timeout from an explicit ``None``."""

    SENTINEL = "use_default"


USE_DEFAULT = UseDefault.SENTINEL


def _elapsed_ms(now: float, since: float | None) -> int | None:
    if since is None:
        return None
    return max(0, round((now - since) * 1000))


async def _join_after_cancellation(task: asyncio.Task[object]) -> None:
    """Join ``task`` despite the joining task being cancelled again mid-join.

    Local re-implementation of ``session_manifest.wait_task_after_cancellation``:
    duplicated rather than imported so this session-layer primitive never
    depends on the higher-level manifest module built on top of it.
    """
    current = asyncio.current_task()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if current is not None:
                current.uncancel()


async def _run_shielded(coro: Coroutine[Any, Any, None]) -> None:
    """Run ``coro`` in a detached task the caller's cancellation cannot reach.

    A bare ``asyncio.shield(coro)`` only survives a single cancellation of the
    awaiting side; a second cancel delivered while the shielded task is still
    running (e.g. an MCP client that force-cancels twice, or an anyio cancel
    scope that keeps re-cancelling until its ``with`` block exits) would
    otherwise let ``CancelledError`` escape mid-cleanup and strand a queued
    waiter or leave a phantom gate owner. Looping the join
    (``_join_after_cancellation``) keeps the detached task's completion
    guaranteed regardless of how many times the caller is cancelled, which is
    essential here: the detached task carries the only in-flight mutation of
    gate ownership, so abandoning the join would leave that mutation's
    outcome unobserved by the gate itself.
    """
    task = asyncio.create_task(coro)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await _join_after_cancellation(task)
        raise


@dataclass(slots=True)
class _Waiter:
    task: asyncio.Task[object]
    operation_name: str
    queued_at: float
    ready: asyncio.Future[None]
    granted: bool = False


@dataclass(frozen=True, slots=True)
class _LeaseToken:
    owner_task: asyncio.Task[object]
    operation_name: str


@dataclass(slots=True)
class _Diagnostics:
    state: OperationGateState
    active_operation: str | None
    active_since: float | None
    queue_depth: int
    oldest_queued_at: float | None
    queue_timeout_seconds: float


class SessionOperationGate:
    """Serializes Playwright operations for one browser session.

    Exactly one asyncio ``Task`` may own the gate at a time, checked by
    ``asyncio.current_task()`` identity. The owning task may re-enter without
    queueing; every other task -- including one the owner spawns itself via
    ``asyncio.create_task`` -- queues FIFO behind it.
    """

    def __init__(
        self,
        instance_id: str,
        kind: str,
        *,
        queue_timeout_seconds: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.kind = kind
        self.queue_timeout_seconds = resolve_operation_queue_timeout_seconds(queue_timeout_seconds)
        self._clock = clock if clock is not None else time.monotonic
        self._admission_lock = asyncio.Lock()
        self._waiters: deque[_Waiter] = deque()
        self._owner_task: asyncio.Task[object] | None = None
        self._root_operation: str | None = None
        self._active_since: float | None = None
        self._depth = 0
        self._queue_depth = 0
        self._state = OperationGateState.OPEN
        self._invariant_reason: str | None = None
        self._diagnostics_lock = threading.Lock()
        self._diagnostics = _Diagnostics(
            state=self._state,
            active_operation=None,
            active_since=None,
            queue_depth=0,
            oldest_queued_at=None,
            queue_timeout_seconds=self.queue_timeout_seconds,
        )

    @staticmethod
    def _current_task() -> asyncio.Task[object]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("SessionOperationGate.operation() must run inside a running asyncio task")
        return task

    def snapshot(self) -> OperationGateSnapshot:
        with self._diagnostics_lock:
            diagnostics = self._diagnostics
        now = self._clock()
        return OperationGateSnapshot(
            state=diagnostics.state.value,
            active_operation=diagnostics.active_operation,
            active_for_ms=_elapsed_ms(now, diagnostics.active_since),
            queue_depth=diagnostics.queue_depth,
            oldest_wait_ms=_elapsed_ms(now, diagnostics.oldest_queued_at),
            queue_timeout_seconds=diagnostics.queue_timeout_seconds,
        )

    def _publish_diagnostics_locked(self) -> None:
        oldest_queued_at = self._waiters[0].queued_at if self._waiters else None
        diagnostics = _Diagnostics(
            state=self._state,
            active_operation=self._root_operation,
            active_since=self._active_since,
            queue_depth=self._queue_depth,
            oldest_queued_at=oldest_queued_at,
            queue_timeout_seconds=self.queue_timeout_seconds,
        )
        with self._diagnostics_lock:
            self._diagnostics = diagnostics

    def _queue_depth_delta(self, delta: int) -> None:
        self._queue_depth += delta
        _QUEUE_DEPTH.add(delta, attributes={"kind": self.kind})

    def _break_locked(self, reason: str) -> None:
        self._state = OperationGateState.BROKEN
        self._invariant_reason = reason

    def _invariant_message(self) -> str:
        return f"operation gate for session {self.instance_id!r} ({self.kind}) is broken: {self._invariant_reason}"

    def _rejection_message(self, name: str, state: OperationGateState) -> str:
        return f"session {self.instance_id!r} operation {name!r} rejected: gate is {state.value} (kind={self.kind!r})"

    def _busy_timeout_message(self, waiter: _Waiter) -> str:
        elapsed = self._clock() - waiter.queued_at
        return (
            f"session {self.instance_id!r} operation {waiter.operation_name!r} timed out "
            f"after {elapsed:.3f}s waiting for the operation gate "
            f"(kind={self.kind!r}, queue_timeout_seconds={self.queue_timeout_seconds})"
        )

    def _raise_if_not_open(self, name: str) -> None:
        state = self._state
        if state is OperationGateState.OPEN:
            return
        reason = state.value
        _REJECTED.add(1, attributes={"operation": name, "kind": self.kind, "reason": reason})
        log.warning(
            "octowright.operation.rejected",
            instance_id=self.instance_id,
            kind=self.kind,
            operation=name,
            state=state.value,
            reason=reason,
        )
        message = self._rejection_message(name, state)
        if state is OperationGateState.CLOSING:
            raise SessionClosingError(message)
        if state is OperationGateState.CLOSED:
            raise SessionClosedError(message)
        raise OperationGateInvariantError(message)

    async def _acquire(
        self,
        operation_name: str,
        wait_timeout_seconds: float | None | UseDefault,
    ) -> _LeaseToken:
        name = validate_operation_name(operation_name)
        task = self._current_task()
        async with self._admission_lock:
            if self._owner_task is task:
                self._depth += 1
                return _LeaseToken(task, name)
            self._raise_if_not_open(name)
            waiter = _Waiter(task, name, self._clock(), asyncio.get_running_loop().create_future())
            self._waiters.append(waiter)
            self._queue_depth_delta(+1)
            log.debug(
                "octowright.operation.queued",
                instance_id=self.instance_id,
                kind=self.kind,
                operation=name,
                state=self._state.value,
                queue_depth=self._queue_depth,
            )
            self._publish_diagnostics_locked()
            self._grant_next_locked()

        timeout = self.queue_timeout_seconds if wait_timeout_seconds is USE_DEFAULT else wait_timeout_seconds
        try:
            if timeout is None:
                await asyncio.shield(waiter.ready)
            else:
                await asyncio.wait_for(asyncio.shield(waiter.ready), timeout=timeout)
        except TimeoutError:
            wait_seconds = self._clock() - waiter.queued_at
            await self._remove_or_release_waiter(waiter, "timeout")
            _QUEUE_TIMEOUT.add(1, attributes={"operation": name, "kind": self.kind})
            log.warning(
                "octowright.operation.timeout",
                instance_id=self.instance_id,
                kind=self.kind,
                operation=name,
                queue_wait_ms=round(wait_seconds * 1000),
            )
            raise SessionBusyTimeoutError(self._busy_timeout_message(waiter)) from None
        except asyncio.CancelledError:
            await self._remove_or_release_waiter(waiter, "cancelled")
            raise
        return _LeaseToken(task, name)

    def _grant_next_locked(self) -> None:
        if self._owner_task is not None or not self._waiters:
            return
        waiter = self._waiters.popleft()
        self._queue_depth_delta(-1)
        waiter.granted = True
        self._owner_task = waiter.task
        self._root_operation = waiter.operation_name
        self._active_since = self._clock()
        self._depth = 1
        wait_seconds = self._active_since - waiter.queued_at
        _QUEUE_WAIT.record(
            wait_seconds,
            attributes={"operation": waiter.operation_name, "kind": self.kind, "outcome": "admitted"},
        )
        log.debug(
            "octowright.operation.admitted",
            instance_id=self.instance_id,
            kind=self.kind,
            operation=waiter.operation_name,
            state=self._state.value,
            queue_depth=self._queue_depth,
            queue_wait_ms=round(wait_seconds * 1000),
        )
        self._publish_diagnostics_locked()
        waiter.ready.set_result(None)

    async def _cleanup_waiter(self, waiter: _Waiter, outcome: Literal["timeout", "cancelled"]) -> None:
        async with self._admission_lock:
            for index, existing in enumerate(self._waiters):
                if existing is waiter:
                    del self._waiters[index]
                    self._queue_depth_delta(-1)
                    _QUEUE_WAIT.record(
                        self._clock() - waiter.queued_at,
                        attributes={"operation": waiter.operation_name, "kind": self.kind, "outcome": outcome},
                    )
                    self._publish_diagnostics_locked()
                    return
            # The waiter is no longer queued, so it must already have been
            # granted by a release that raced this cleanup (see the
            # module-level ``SessionOperationGate`` docstring for context):
            # the ticket lost the race for a legitimate outcome (timeout or
            # cancellation) but won the FIFO grant anyway. Nobody else holds
            # a lease for it -- ``_acquire`` never returned one -- so undo the
            # grant here instead of leaving a phantom owner that will never
            # run its body and never release.
            if waiter.granted and self._owner_task is waiter.task:
                self._owner_task = None
                self._root_operation = None
                self._active_since = None
                self._depth = 0
                self._grant_next_locked()
                self._publish_diagnostics_locked()

    async def _remove_or_release_waiter(self, waiter: _Waiter, outcome: Literal["timeout", "cancelled"]) -> None:
        await _run_shielded(self._cleanup_waiter(waiter, outcome))

    def _record_active_duration_locked(self, outcome: Literal["ok", "error", "cancelled"]) -> None:
        if self._active_since is None or self._root_operation is None:
            return
        duration = self._clock() - self._active_since
        _ACTIVE_DURATION.record(
            duration,
            attributes={"operation": self._root_operation, "kind": self.kind, "outcome": outcome},
        )
        level = log.debug if outcome == "ok" else log.info
        level(
            "octowright.operation.released",
            instance_id=self.instance_id,
            kind=self.kind,
            operation=self._root_operation,
            state=self._state.value,
            outcome=outcome,
            active_duration_ms=round(duration * 1000),
        )

    async def _release(self, lease: _LeaseToken, outcome: Literal["ok", "error", "cancelled"]) -> None:
        async with self._admission_lock:
            if self._owner_task is not lease.owner_task:
                self._break_locked("operation released by a task that does not own the gate")
                self._publish_diagnostics_locked()
                raise OperationGateInvariantError(self._invariant_message())
            self._depth -= 1
            if self._depth:
                return
            self._record_active_duration_locked(outcome)
            self._owner_task = None
            self._root_operation = None
            self._active_since = None
            self._grant_next_locked()
            self._publish_diagnostics_locked()

    @asynccontextmanager
    async def operation(
        self,
        operation_name: LiteralString,
        *,
        wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT,
    ) -> AsyncIterator[None]:
        lease = await self._acquire(operation_name, wait_timeout_seconds)
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
            # ``asyncio.shield`` wraps ``self._release(...)`` in a brand-new
            # Task, so checking ``asyncio.current_task()`` identity from
            # inside ``_release`` would compare against that detached task,
            # not the caller that actually owns the gate -- it must compare
            # against the ``_LeaseToken`` captured above instead, or every
            # cancellation-safe exit would falsely trip the ownership
            # invariant.
            await _run_shielded(self._release(lease, outcome))
