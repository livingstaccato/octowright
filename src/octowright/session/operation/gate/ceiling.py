# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The opt-in active-duration ceiling -- ``SessionOperationGate.enforce_active_timeout``.

Split out of ``core.py`` (kept under the repository's LOC-per-file
convention), mirroring how ``close.py``'s ``_CloseGateMixin`` splits out the
close-reservation machinery. Every method here only ever runs as part of a
composed ``SessionOperationGate`` instance -- see that class and its
``operation()`` method (``core.py``) for the other half of the mechanism
this backstop depends on (the cancellation-absorption check that keeps a
ceiling breach from reaching an in-flight caller as a bare
``asyncio.CancelledError`` -- 2026-08-29 hang-resilience whole-branch
review, finding F1).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping

from provide.telemetry import get_logger

from octowright._tracing import counter
from octowright.session.operation.gate.types import OperationGateState, _positive_finite_seconds

log = get_logger(__name__)

# OFF by default (see resolve_operation_active_timeout_seconds) -- the ceiling
# is a backstop for unenumerated call sites, and cancelling in-flight browser
# work is a heavier intervention than Task 1's per-call budget failing one
# call. Mirrors session/timeouts.py's _OFF_TOKENS plus "" for an env var set
# to the empty string, which os.environ.get(..., default) alone would not
# catch (an explicit empty value is not "unset").
_OPERATION_ACTIVE_TIMEOUT_ENV = "OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS"
_ACTIVE_TIMEOUT_OFF_TOKENS = frozenset({"", "0", "off", "never", "none", "disabled", "false", "no"})


def resolve_operation_active_timeout_seconds(
    explicit: float | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> float | None:
    """Resolve the opt-in active-duration ceiling. ``None`` means disabled.

    Same parser shape as ``core.resolve_operation_queue_timeout_seconds``
    with one deliberate difference. That resolver defaults ON (a per-call
    budget trades an unbounded hang for a bounded one), so an unparsable
    value there falls back to a working default rather than silently
    reintroducing the hang. This ceiling defaults OFF -- cancelling
    in-flight browser work is a heavier intervention than failing one call
    -- so an unparsable value falls back to OFF too: the feature itself is
    opt-in, and a typo in the env var must not silently turn on a hardcoded
    quota nobody asked for.

    ``explicit`` (a direct caller override, not an env var) is still
    validated strictly and raises on an invalid value -- that path is a
    programming error, not an operator typo, and Task 1's resolver treats
    its own ``explicit`` parameter the same way.
    """
    if explicit is not None:
        return _positive_finite_seconds(explicit, source="operation_active_timeout_seconds")
    source = os.environ if environ is None else environ
    raw = source.get(_OPERATION_ACTIVE_TIMEOUT_ENV)
    if raw is None or raw.strip().lower() in _ACTIVE_TIMEOUT_OFF_TOKENS:
        return None
    try:
        return _positive_finite_seconds(raw, source=_OPERATION_ACTIVE_TIMEOUT_ENV)
    except ValueError:
        return None


_ACTIVE_TIMEOUT = counter("octowright_operation_active_timeout_total")


class _CeilingGateMixin:
    """The active-duration ceiling breaker.

    Field/stub-method declarations below (never assigned here) describe the
    host's real attributes and core scheduler methods so mypy can check
    this mixin in isolation, matching ``_CloseGateMixin``'s own pattern
    (``close.py``) -- without a nominal (and circular) import of the
    concrete class itself. Kept in sync by hand -- there is no runtime
    enforcement.
    """

    _admission_lock: asyncio.Lock
    _state: OperationGateState
    _owner_task: asyncio.Task[object] | None
    _root_operation: str | None
    _active_since: float | None
    _ceiling_cancelled_task: asyncio.Task[object] | None
    kind: str
    instance_id: str
    _clock: Callable[[], float]

    def _break_locked(self, reason: str) -> None: ...
    def _publish_diagnostics_locked(self) -> None: ...

    async def enforce_active_timeout(self, ceiling_seconds: float) -> bool:
        """Cancel the active (root) operation if it has run past *ceiling_seconds*.

        The backstop for the Playwright call sites nobody has bounded with
        ``session/timeouts.bounded`` yet: rather than a per-call budget, this
        reads what the gate already tracks for its ROOT lease --
        ``_active_since`` and ``_root_operation`` -- and, if one operation
        has held the gate open at least as long as the ceiling, cancels the
        owning task and drives the gate to ``broken`` through the same
        ``_break_locked`` invariant path every other broken-gate transition
        uses. A subsequent operation on this gate then fails fast with the
        ordinary ``OperationGateInvariantError`` rejection -- no new error
        type, no new state, nothing for a caller to special-case. The ONE
        caller the ceiling actually cancels sees something different: see
        ``core.py``'s ``operation()`` docstring for why that in-flight
        caller gets ``SessionOperationAbortedError`` instead of a bare
        ``asyncio.CancelledError`` (F1, 2026-08-29 hang-resilience
        whole-branch review) -- this method's job is only to request the
        cancellation and mark who it targeted; the conversion itself lives
        where the exception is actually caught.

        Called from ``housekeeping.py``'s periodic loop (job 6 -- see its
        module docstring), never from a per-gate background task: one timer
        per session multiplied across a large pool is real overhead for a
        rare event, and housekeeping already walks every session on its own
        interval. A gate whose active operation is still inside budget (the
        overwhelming common case, and the ENTIRE case when the feature is
        off) costs one uncontended lock acquire and nothing else -- no
        Playwright call, no per-session task.

        Acts on ``OPEN`` **and** ``CLOSING`` (never ``CLOSED``/``BROKEN``,
        which have no active root lease left to break). Closing a wedged
        session is the first thing a human or agent reaches for, and
        ``reserve_close`` queues the close reservation's waiter behind
        whichever owner already holds the gate rather than granting it --
        that owner is exactly what this method may need to cancel, so
        checking ``OPEN`` only would disarm the ceiling the moment a close
        was requested and leave ``reservation.wait()`` hanging forever
        instead. Breaking here still resolves the close instead of
        stranding it: ``_break_locked``'s ``_fail_queued_locked`` fails the
        queued close waiter, which resolves ``reservation.wait()`` with an
        error (not a hang) for the caller, and ``_coordinate_close``'s own
        ``finally`` block runs regardless of whether ``close_operation``'s
        body ever executed, so the pool's bookkeeping (``_sessions``,
        ``_closing_sessions``, the manifest, the close-event publish) still
        drains -- verified end to end in
        ``tests/test_operation_gate_integration.py::test_active_timeout_ceiling_unwedges_a_close_in_progress``.
        The one thing that does NOT happen on this path: the actual
        Playwright teardown inside ``close_operation``'s body never runs for
        a reservation that was still queued -- but an unclosed browser
        process on a session that was already wedged and never going to
        close cleanly either way predates this feature; what changes is
        that the caller and the pool's own bookkeeping are no longer stuck
        waiting on it.

        This method can ALSO cancel a close reservation that was already
        granted and is itself wedged mid-teardown (e.g. a hung
        ``context.close()``). Such a cancellation can land in more than one
        place -- swallowed and returned by ``prepare_then_teardown``, or
        raised in the close body before it ever runs -- and every one of
        them is normalized in a SINGLE seam, ``_terminal_close_failure``
        (``operation/gate/close.py``), into ``SessionCloseAbortedError``.
        ``_release_close`` deliberately converts nothing itself, so one
        cause cannot yield two error types depending on where it landed; an
        earlier design did exactly that, and a ceiling-aborted close then
        read as an ordinary close race. See that class's docstring
        (``operation/gate/types.py``) for the full mechanism, and
        ``relaunch._close_with_fallback_snapshot`` for why the distinction
        from a plain ``SessionClosedError`` matters.

        Deliberately does NOT raise or publish a ``SessionCallTimeoutError``.
        That machinery (``session/timeouts.bounded``, this gate's
        ``on_call_timeout`` hook) exists for a call site that knows its own
        operation and awaits it under a budget from the INSIDE. Cancelling
        from the OUTSIDE, after the fact, is a different failure shape: the
        exception delivered to the owning task starts as a plain
        ``asyncio.CancelledError`` with no ``__cause__`` linking it to a
        ``SessionCallTimeoutError``, so ``operation()``'s own ``finally``
        block (the innermost-lease publish ``on_call_timeout`` machinery)
        does not fire for a ceiling breach, PROVIDED the gated code under
        the owning task does not itself convert that ``CancelledError`` into
        a different exception type (e.g. catching it and raising a
        ``SessionCallTimeoutError(...) from ce``) -- no production call site
        does this today (checked every ``except asyncio.CancelledError`` /
        ``except BaseException`` site that can swallow a cancellation; the
        one that can re-raises the identical object), so in practice a
        single wedge produces exactly one signal, never both an
        "unresponsive" ``SessionCrashedEvent`` AND a ceiling-breach invariant
        break that would contradict each other. The two backstops report
        through deliberately separate channels: a per-call timeout that
        escapes a gated operation is a target that answered *its own*
        budget overrun (``on_call_timeout``); a ceiling breach is the gate
        itself noticing nobody put a budget on the call at all. (This holds
        regardless of F1's fix converting the ceiling's OWN CancelledError
        into ``SessionOperationAbortedError`` at ``operation()``'s boundary
        -- that conversion happens AFTER this check, and produces neither a
        ``SessionCallTimeoutError`` nor anything with one in its
        ``__cause__`` chain, so it does not newly trigger ``on_call_timeout``
        either.)

        Returns True if a breach was found and handled, else False. The
        caller (``housekeeping._enforce_operation_active_timeout_once``)
        checks each session independently and isolates a per-session
        failure, so one wedged session's gate breaking never stops another
        session's (healthy or also-wedged) gate from being checked.
        """
        async with self._admission_lock:
            if self._state not in (OperationGateState.OPEN, OperationGateState.CLOSING):
                return False
            owner = self._owner_task
            operation = self._root_operation
            active_since = self._active_since
            if owner is None or operation is None or active_since is None:
                return False
            duration = self._clock() - active_since
            if duration < ceiling_seconds:
                return False
            # F4 (same review): owner.cancel() can return False if the task
            # already finished -- its body returned and it is draining its
            # OWN release via _run_shielded, which needs this SAME
            # admission_lock this check holds, so that drain genuinely
            # cannot have completed yet. Breaking an otherwise-healthy gate
            # (and, per _run_shielded's re-raise-after-join, turning that
            # caller's successful result into a stray CancelledError) over
            # an operation that was never actually wedged would be strictly
            # worse than doing nothing -- check BEFORE recording anything.
            self._ceiling_cancelled_task = owner
            if not owner.cancel():
                self._ceiling_cancelled_task = None
                return False
            _ACTIVE_TIMEOUT.add(1, attributes={"operation": operation, "kind": self.kind})
            log.warning(
                "octowright.operation.active_timeout",
                instance_id=self.instance_id,
                kind=self.kind,
                operation=operation,
                active_duration_ms=round(duration * 1000),
                ceiling_seconds=ceiling_seconds,
            )
            self._break_locked(f"operation {operation!r} exceeded the active-duration ceiling of {ceiling_seconds}s")
            self._publish_diagnostics_locked()
            return True
