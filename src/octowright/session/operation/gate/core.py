# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import functools
import os
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Concatenate, Literal, LiteralString, ParamSpec, TypedDict, TypeVar

from provide.telemetry import get_logger

from octowright._tracing import counter, gauge, histogram
from octowright.session.operation.gate.ceiling import _CeilingGateMixin
from octowright.session.operation.gate.close import _CloseGateMixin
from octowright.session.operation.gate.types import (
    CloseReservation,
    OperationGateInvariantError,
    OperationGateState,
    SessionBusyTimeoutError,
    SessionCloseAbortedError,
    SessionClosedError,
    SessionClosingError,
    SessionOperationAbortedError,
    _LeaseToken,
    _positive_finite_seconds,
    _run_shielded,
    _Waiter,
    validate_operation_name,
)
from octowright.session.timeouts import SessionCallTimeoutError

__all__ = [
    "USE_DEFAULT",
    "CloseReservation",
    "OperationGateInvariantError",
    "OperationGateSnapshot",
    "OperationGateState",
    "SessionBusyTimeoutError",
    "SessionCloseAbortedError",
    "SessionClosedError",
    "SessionClosingError",
    "SessionOperationAbortedError",
    "SessionOperationGate",
    "UseDefault",
    "gated_operation",
    "resolve_operation_queue_timeout_seconds",
    "validate_operation_name",
]

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_OPERATION_QUEUE_TIMEOUT_SECONDS = 300.0
_OPERATION_TIMEOUT_ENV = "OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS"


class OperationGateSnapshot(TypedDict):
    state: Literal["open", "closing", "closed", "broken"]
    active_operation: str | None
    active_for_ms: int | None
    queue_depth: int
    oldest_wait_ms: int | None
    queue_timeout_seconds: float


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


_QUEUE_WAIT = histogram("octowright_operation_queue_wait_seconds", unit="s")
_ACTIVE_DURATION = histogram("octowright_operation_active_duration_seconds", unit="s")
_QUEUE_TIMEOUT = counter("octowright_operation_queue_timeout_total")
_REJECTED = counter("octowright_operation_rejected_total")
_QUEUE_DEPTH = gauge("octowright_operation_queue_depth", unit="1")

log = get_logger(__name__)

# How many `__cause__` hops _call_timeout_cause walks looking for a
# SessionCallTimeoutError. Bounded so a pathological/cyclic cause chain can't
# spin the check; four comfortably covers every wrapping layer in this
# codebase today (a macro action's SessionCallTimeoutError, wrapped once by
# macros/execution.py's `raise RuntimeError(payload) from exc`, wrapped again
# by a scenario or macro-sequence caller).
_CALL_TIMEOUT_CAUSE_MAX_HOPS = 4


def _call_timeout_cause(
    exc: BaseException | None,
    *,
    max_hops: int = _CALL_TIMEOUT_CAUSE_MAX_HOPS,
) -> SessionCallTimeoutError | None:
    """Find a ``SessionCallTimeoutError`` in *exc*'s explicit cause chain.

    A bare ``isinstance(exc, SessionCallTimeoutError)`` only catches a caller
    that never wraps the error. ``macros/execution.py``'s per-action failure
    handling instead re-raises every action failure as
    ``RuntimeError(payload) from exc``, so an unwrapped isinstance check
    never saw the ``SessionCallTimeoutError`` that caused it. Walking
    ``__cause__`` (set by Python's ``raise ... from exc``, never by a bare
    ``raise`` inside an ``except`` block) finds it regardless of how many
    such layers wrap it, bounded by *max_hops* so a pathological or cyclic
    chain cannot spin forever.
    """
    for _ in range(max_hops):
        if isinstance(exc, SessionCallTimeoutError):
            return exc
        if exc is None:
            return None
        exc = exc.__cause__
    return None


# Marks a SessionCallTimeoutError instance (never a wrapper of one -- see
# _call_timeout_cause, which always returns the SAME underlying instance
# regardless of how many `raise ... from exc` layers wrap it) once its
# on_call_timeout hook has fired, so an ancestor `operation()` frame that
# later sees the SAME exception object propagate through it (nested/reentrant
# calls, or a caller that re-raises `from exc` without swallowing) does not
# fire it again. A plain instance attribute rather than a class/module set:
# the exception object IS the one thing every frame in the propagation path
# shares, so marking it directly needs no separate registry to clean up.
_CALL_TIMEOUT_PUBLISHED_ATTR = "_octowright_call_timeout_published"


def _mark_call_timeout_published(cause: SessionCallTimeoutError) -> bool:
    """Return True and mark *cause* published, or False if already marked."""
    if getattr(cause, _CALL_TIMEOUT_PUBLISHED_ATTR, False):
        return False
    setattr(cause, _CALL_TIMEOUT_PUBLISHED_ATTR, True)
    return True


class UseDefault(Enum):
    """Sentinel distinguishing the gate's configured timeout from an explicit ``None``."""

    SENTINEL = "use_default"


USE_DEFAULT = UseDefault.SENTINEL


def _elapsed_ms(now: float, since: float | None) -> int | None:
    if since is None:
        return None
    return max(0, round((now - since) * 1000))


@dataclass(slots=True)
class _Diagnostics:
    state: OperationGateState
    active_operation: str | None
    active_since: float | None
    queue_depth: int
    oldest_queued_at: float | None
    queue_timeout_seconds: float


class SessionOperationGate(_CloseGateMixin, _CeilingGateMixin):
    """Serializes Playwright operations for one browser session.

    Exactly one asyncio ``Task`` may own the gate at a time, checked by
    ``asyncio.current_task()`` identity. The owning task may re-enter without
    queueing; every other task -- including one the owner spawns itself via
    ``asyncio.create_task`` -- queues FIFO behind it. Close/external-close/
    control-plane methods come from ``_CloseGateMixin``; the active-duration
    ceiling (``enforce_active_timeout``) comes from ``_CeilingGateMixin`` --
    both part of this API.
    """

    def __init__(
        self,
        instance_id: str,
        kind: str,
        *,
        queue_timeout_seconds: float | None = None,
        clock: Callable[[], float] | None = None,
        on_call_timeout: Callable[[str, SessionCallTimeoutError], None] | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.kind = kind
        self.queue_timeout_seconds = resolve_operation_queue_timeout_seconds(queue_timeout_seconds)
        self._clock = clock if clock is not None else time.monotonic
        # Invoked at most once per SessionCallTimeoutError that escapes the
        # ROOT gated operation (see operation()'s finally block) -- never for
        # a nested reentrant frame, and never for any other exception type.
        # None for a gate built without a session to notify (bare unit tests).
        self._on_call_timeout = on_call_timeout
        self._admission_lock = asyncio.Lock()
        self._waiters: deque[_Waiter] = deque()
        self._owner_task: asyncio.Task[object] | None = None
        self._root_operation: str | None = None
        self._active_since: float | None = None
        self._depth = 0
        self._queue_depth = 0
        self._state = OperationGateState.OPEN
        self._invariant_reason: str | None = None
        self._close_reservation: CloseReservation | None = None
        self._granted_close_reservation: CloseReservation | None = None
        # F1 (2026-08-29 hang-resilience whole-branch review): which task's
        # cancellation `enforce_active_timeout` itself requested, and the
        # `Task.cancelling()` count the CURRENT owner had at the moment it
        # became one -- together let `operation()` tell "the ceiling cancelled
        # ME" apart from any other cancellation reaching the same task (a
        # genuine client disconnect, daemon shutdown), the same uncancel()
        # -and-compare pattern `asyncio.timeout.__aexit__` uses on itself.
        # Both reset on every release; see `_grant_next_locked`/`_release`/
        # `_cleanup_waiter` (ordinary leases) and `close_operation` (close
        # reservations, `close.py`) for where the baseline is (re)captured.
        self._ceiling_cancelled_task: asyncio.Task[object] | None = None
        self._owner_cancel_baseline: int | None = None
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
        self._fail_queued_locked(OperationGateInvariantError, reason=reason)
        log.error(
            "octowright.operation.invariant_broken",
            instance_id=self.instance_id,
            kind=self.kind,
            state=self._state.value,
            reason=reason,
        )

    def _fail_queued_locked(self, error_cls: type[RuntimeError], *, reason: str) -> None:
        waiters, self._waiters = list(self._waiters), deque()
        if waiters:
            self._queue_depth_delta(-len(waiters))
        for waiter in waiters:
            if waiter.ready.done():
                continue
            self._log_rejected(waiter.operation_name, reason)
            waiter.ready.set_exception(error_cls(self._rejection_message(waiter.operation_name, self._state)))
        self._publish_diagnostics_locked()

    def _log_rejected(self, name: str, reason: str) -> None:
        _REJECTED.add(1, attributes={"operation": name, "kind": self.kind, "reason": reason})
        log.warning(
            "octowright.operation.rejected",
            instance_id=self.instance_id,
            kind=self.kind,
            operation=name,
            state=self._state.value,
            reason=reason,
        )

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
        self._log_rejected(name, state.value)
        message = self._rejection_message(name, state)
        if state is OperationGateState.CLOSING:
            raise SessionClosingError(message)
        if state is OperationGateState.CLOSED:
            raise SessionClosedError(message)
        raise OperationGateInvariantError(message)

    async def _acquire(
        self,
        operation_name: str,
        wait_timeout_seconds: float | UseDefault | None,
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
        except BaseException:
            # Not a timeout, not a plain cancellation -- e.g. GeneratorExit
            # from an abandoned async-generator finalization. Reachability
            # through the intended API is essentially nil, but leaving this
            # waiter queued would eventually make ``_grant_next_locked`` hand
            # ownership to a task that will never run its body and never
            # release, wedging the gate for everyone behind it.
            await self._remove_or_release_waiter(waiter, "cancelled")
            raise
        return _LeaseToken(task, name)

    def _grant_next_locked(self) -> None:
        if self._owner_task is not None or self._granted_close_reservation is not None or not self._waiters:
            return
        waiter = self._waiters.popleft()
        self._queue_depth_delta(-1)
        waiter.granted = True
        self._root_operation = waiter.operation_name
        self._active_since = self._clock()
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
        if waiter.task is None:
            # Close waiter: no coordinator task exists yet (see
            # close_operation). _granted_close_reservation is a sentinel that
            # keeps the guard above treating the gate as occupied across the
            # grant-to-bind gap, instead of assigning a nonexistent owner.
            reservation = self._close_reservation
            if reservation is None or reservation.waiter is not waiter:
                self._break_locked("close waiter granted without a retained reservation")
                self._publish_diagnostics_locked()
                return
            self._granted_close_reservation = reservation
            self._depth = 0
        else:
            self._owner_task = waiter.task
            # Baseline for the ceiling's uncancel()-and-compare check
            # (operation()'s except-CancelledError branch) -- captured HERE,
            # at grant time, not inside enforce_active_timeout itself: the
            # ceiling fires from a different task, arbitrarily later, and
            # needs to know how many cancellations were ALREADY pending on
            # this task before it adds its own.
            self._owner_cancel_baseline = waiter.task.cancelling()
            self._depth = 1
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
                self._owner_cancel_baseline = None
                self._ceiling_cancelled_task = None
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
            self._owner_cancel_baseline = None
            self._ceiling_cancelled_task = None
            self._grant_next_locked()
            self._publish_diagnostics_locked()

    @asynccontextmanager
    async def operation(
        self,
        operation_name: LiteralString,
        *,
        wait_timeout_seconds: float | UseDefault | None = USE_DEFAULT,
    ) -> AsyncIterator[None]:
        """Acquire (or re-enter) the gate's lease for *operation_name*.

        F1 (2026-08-29 hang-resilience whole-branch review, CRITICAL): a
        cancellation ``enforce_active_timeout`` requested on THIS lease's
        owner must never reach the caller as a bare ``asyncio.CancelledError``.
        In the daemon, that owning task either IS the MCP request task, or
        is awaited by it through ``asyncio.shield`` (the idempotency cache),
        and a ``CancelledError`` is a ``BaseException`` the ``mcp`` library
        does not convert -- it reaches the JSON-RPC dispatcher's own
        cancellation handling, which reports ``"Connection closed"`` and
        tears down the WHOLE connection, taking every other concurrent call
        on it down too. Driven end to end against a real ``MCPServer`` +
        ``ClientSession`` before this fix: a ceiling breach on one wedged
        call answered a concurrent, healthy ``ping`` with the same error,
        and the server's own ``run()`` returned -- the session was over.

        The ``except asyncio.CancelledError`` branch below tells "the
        ceiling cancelled ME" apart from any other cancellation reaching the
        same task (a genuine client disconnect, daemon shutdown) using
        ``asyncio.timeout.__aexit__``'s own pattern: ``_grant_next_locked``
        (and ``close_operation`` for a close reservation) records
        ``task.cancelling()`` as ``_owner_cancel_baseline`` the moment this
        task becomes the gate's owner; ``enforce_active_timeout`` records
        the SAME task as ``_ceiling_cancelled_task`` right before calling
        ``.cancel()`` on it. If, when the ``CancelledError`` arrives here,
        this lease's owner is that recorded task AND ``uncancel()`` brings
        the count back to (or below) that baseline -- meaning no OTHER
        cancellation is still layered on top -- the ceiling's own
        contribution is the only one in flight, so it is safe to convert.
        Naming one specific task (not "any CancelledError") is what keeps a
        genuine cancellation propagating normally: ``uncancel()`` is called
        ONLY when the marker confirms the ceiling is the one that asked, and
        exactly once, mirroring how ``asyncio.timeout`` only ever absorbs
        its OWN cancellation.
        """
        lease = await self._acquire(operation_name, wait_timeout_seconds)
        outcome: Literal["ok", "error", "cancelled"] = "ok"
        error: BaseException | None = None
        try:
            yield
        except asyncio.CancelledError:
            outcome = "cancelled"
            if self._ceiling_cancelled_task is lease.owner_task:
                self._ceiling_cancelled_task = None
                baseline = self._owner_cancel_baseline
                if baseline is not None and lease.owner_task.uncancel() <= baseline:
                    raise SessionOperationAbortedError(
                        f"session {self.instance_id!r} operation {lease.operation_name!r} was "
                        f"aborted: the active-duration ceiling cancelled it after it exceeded its "
                        f"budget (kind={self.kind!r}); this gate is now broken -- relaunch the "
                        f"session"
                    ) from None
            raise
        except BaseException as exc:
            outcome = "error"
            error = exc
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
            # Fire from the INNERMOST `operation()` frame that sees a
            # SessionCallTimeoutError escape it -- not "the root lease", which
            # assumes the timeout always makes it all the way out. It does
            # not: macros/artifacts.py's macro_artifact_run swallows the
            # error from its own nested run_macro() lease inside its OWN
            # root "macro_artifact_run" lease, and run_sequence(stop_on_
            # failure=False) does the same per failed step -- in both shapes
            # nothing ever escapes a root frame for a root-only check to see.
            # Every `operation()` frame the exception actually passes through
            # runs this same check, but Python unwinds try/finally
            # inside-out, so the innermost frame's finally always runs
            # first; _mark_call_timeout_published marks the SessionCall
            # TimeoutError instance itself (not any wrapper -- see
            # _call_timeout_cause, which always returns that same underlying
            # instance) the first time it is seen, so an outer frame that
            # also sees it (still propagating, or reachable via its own
            # __cause__ walk) finds the mark and stays silent. Exactly one
            # publish per wedge, regardless of who swallows or re-wraps the
            # exception on the way out -- and using THIS frame's own
            # operation_name means a nested wedge reports the specific
            # action that actually stalled, not an outer umbrella name.
            if self._on_call_timeout is not None:
                cause = _call_timeout_cause(error)
                if cause is not None and _mark_call_timeout_published(cause):
                    try:
                        self._on_call_timeout(lease.operation_name, cause)
                    except Exception:
                        # The hook (session -> event bus) must never mask the
                        # real exception still propagating out of this context
                        # manager.
                        log.exception(
                            "octowright.operation.call_timeout_hook_failed",
                            instance_id=self.instance_id,
                            kind=self.kind,
                            operation=lease.operation_name,
                        )


def gated_operation(
    operation_name: LiteralString,
) -> Callable[
    [Callable[Concatenate[Any, P], Coroutine[Any, Any, R]]],
    Callable[Concatenate[Any, P], Coroutine[Any, Any, R]],
]:
    """Wrap an async session method so every call runs under a fixed operation lease.

    ``operation_name`` is validated at decoration time (import time), not per
    call -- a typo in a fixed literal fails the test suite immediately rather
    than surfacing as a runtime ``ValueError`` on first invocation. The wrapper
    reads ``self.operation`` dynamically (not a captured gate) so it works on
    any object exposing the ``operation()`` context-manager surface, matching
    ``SessionLike`` rather than binding to ``SessionOperationGate`` directly.
    Reentrant by construction: ``self.operation(...)`` re-enters the caller's
    existing lease when the same task already owns the gate, so a decorated
    method calling another decorated method on the same session never queues
    behind itself and the root operation name stays the outermost one.

    Typed with ``Concatenate[Any, P]`` / ``Coroutine[Any, Any, R]`` rather
    than ``object`` / ``Awaitable[R]``: a concrete self type (e.g.
    ``SessionPageMixin``) narrower than ``object`` fails the decorator's
    parameter-contravariance check, and ``Awaitable[R]`` is a wider return
    type than the ``Coroutine[Any, Any, R]`` every ``async def`` actually
    returns, which trips a Liskov override error against ``SessionLike``'s
    declared async signatures. ``Any`` sidesteps both without changing
    runtime behavior.
    """
    fixed_name = validate_operation_name(operation_name)

    def _decorate(
        function: Callable[Concatenate[Any, P], Coroutine[Any, Any, R]],
    ) -> Callable[Concatenate[Any, P], Coroutine[Any, Any, R]]:
        @functools.wraps(function)
        async def _wrapped(self: Any, *args: P.args, **kwargs: P.kwargs) -> R:
            operation = self.operation
            async with operation(fixed_name):
                return await function(self, *args, **kwargs)

        return _wrapped

    return _decorate
