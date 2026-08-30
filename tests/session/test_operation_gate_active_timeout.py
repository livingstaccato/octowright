# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the opt-in active-duration ceiling (Task 3 of the hang-resilience plan).

Task 1 bounds the Playwright call sites that are known today
(``session/timeouts.bounded``). This ceiling is the backstop for the ones
nobody has found yet: the gate already tracks how long its root operation has
been active, so ``SessionOperationGate.enforce_active_timeout`` can notice one
that has run impossibly long without any call site enumeration. Unlike Task 1
it is OFF by default -- cancelling in-flight browser work is heavier than
failing one call.

Uses the fake-clock pattern from ``tests/session/test_operation_gate.py``
(a mutable closure standing in for ``time.monotonic``) rather than real
sleeping, so these tests run in milliseconds regardless of the configured
ceiling. Every ``await`` that should resolve promptly is still wrapped in an
outer ``asyncio.timeout`` -- a test that guards against a hang must not itself
hang on a regression.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from octowright.session import operation_gate
from octowright.session.operation_gate import (
    OperationGateInvariantError,
    SessionOperationGate,
    resolve_operation_active_timeout_seconds,
)

_ENV = "OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS"


class _MetricCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, str] | None]] = []

    def add(self, amount: float, attributes: dict[str, str] | None = None) -> None:
        self.calls.append((amount, attributes))


def _make_clock(start: float = 0.0) -> tuple[Callable[[], float], Callable[[float], None]]:
    state = {"now": start}

    def clock() -> float:
        return state["now"]

    def advance(delta: float) -> None:
        state["now"] += delta

    return clock, advance


# --- Resolver: off by default, falsey tokens, unparsable falls back to OFF ---


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert resolve_operation_active_timeout_seconds() is None


@pytest.mark.parametrize("token", ["", "0", "off", "never", "none", "disabled", "false", "no"])
def test_falsey_tokens_keep_it_off(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, token)
    assert resolve_operation_active_timeout_seconds() is None


@pytest.mark.parametrize("raw", ["abc", "-5", "nan", "inf", "-inf"])
def test_unparsable_or_nonpositive_falls_back_to_off(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The feature itself is opt-in, so -- unlike Task 1's per-call budget,
    which falls back to a working default -- a typo here must not silently
    turn on a hardcoded ceiling nobody asked for."""
    monkeypatch.setenv(_ENV, raw)
    assert resolve_operation_active_timeout_seconds() is None


def test_valid_value_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "120")
    assert resolve_operation_active_timeout_seconds() == 120.0


def test_explicit_override_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "120")
    assert resolve_operation_active_timeout_seconds(30.0) == 30.0


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan")])
def test_explicit_invalid_override_raises(value: float) -> None:
    # explicit is a caller override (e.g. a future BrowserPool knob), not an
    # operator-typed env var -- a bad value there is a programming error and
    # is validated strictly, matching resolve_operation_queue_timeout_seconds.
    with pytest.raises(ValueError):
        resolve_operation_active_timeout_seconds(value)


# --- Gate behavior ---


@pytest.mark.asyncio
async def test_exceeding_ceiling_cancels_owner_and_breaks_gate() -> None:
    clock, advance = _make_clock()
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30, clock=clock)
    owner_entered = asyncio.Event()
    never = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("wedged"):
            owner_entered.set()
            await never.wait()

    owner_task = asyncio.create_task(owner())
    async with asyncio.timeout(5):
        await owner_entered.wait()

    advance(100.0)
    async with asyncio.timeout(5):
        assert await gate.enforce_active_timeout(60.0) is True

    async with asyncio.timeout(5):
        with pytest.raises(asyncio.CancelledError):
            await owner_task

    assert gate.snapshot()["state"] == "broken"


@pytest.mark.asyncio
async def test_subsequent_operation_rejected_fast_not_queued() -> None:
    clock, advance = _make_clock()
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30, clock=clock)
    owner_entered = asyncio.Event()
    never = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("wedged"):
            owner_entered.set()
            await never.wait()

    owner_task = asyncio.create_task(owner())
    async with asyncio.timeout(5):
        await owner_entered.wait()

    advance(100.0)
    async with asyncio.timeout(5):
        assert await gate.enforce_active_timeout(60.0) is True
    async with asyncio.timeout(5):
        with pytest.raises(asyncio.CancelledError):
            await owner_task

    async def later() -> None:
        async with gate.operation("after"):
            pass

    # A broken gate rejects immediately in _acquire, before a waiter is ever
    # appended -- not queued behind queue_timeout_seconds.
    async with asyncio.timeout(1):
        with pytest.raises(OperationGateInvariantError, match="broken"):
            await later()
    assert gate.snapshot()["queue_depth"] == 0


@pytest.mark.asyncio
async def test_operation_finishing_inside_ceiling_is_never_touched() -> None:
    clock, advance = _make_clock()
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30, clock=clock)

    async with gate.operation("quick"):
        advance(5.0)
        async with asyncio.timeout(5):
            assert await gate.enforce_active_timeout(60.0) is False

    assert gate.snapshot()["state"] == "open"
    async with gate.operation("after"):
        assert gate.snapshot()["active_operation"] == "after"


@pytest.mark.asyncio
async def test_metric_increments_exactly_once_per_breach(monkeypatch: pytest.MonkeyPatch) -> None:
    metric_cap = _MetricCapture()
    monkeypatch.setattr(operation_gate, "_ACTIVE_TIMEOUT", metric_cap)
    clock, advance = _make_clock()
    gate = SessionOperationGate("one", "chromium", queue_timeout_seconds=30, clock=clock)
    owner_entered = asyncio.Event()
    never = asyncio.Event()

    async def owner() -> None:
        async with gate.operation("wedged"):
            owner_entered.set()
            await never.wait()

    owner_task = asyncio.create_task(owner())
    async with asyncio.timeout(5):
        await owner_entered.wait()

    advance(100.0)
    async with asyncio.timeout(5):
        assert await gate.enforce_active_timeout(60.0) is True
        # A second look at the same (now broken) gate must not double-count --
        # a housekeeping cycle that runs again before the owner finishes
        # unwinding must see the state is no longer OPEN and return early.
        assert await gate.enforce_active_timeout(60.0) is False

    async with asyncio.timeout(5):
        with pytest.raises(asyncio.CancelledError):
            await owner_task

    assert len(metric_cap.calls) == 1
    amount, attributes = metric_cap.calls[0]
    assert amount == 1
    assert attributes == {"operation": "wedged", "kind": "chromium"}


@pytest.mark.asyncio
async def test_other_gates_are_unaffected_by_one_gates_breach() -> None:
    """The whole point: one wedged session's gate breaking must never touch
    another session's gate, even when both are checked in the same pass."""
    wedged_clock, wedged_advance = _make_clock()
    healthy_clock, healthy_advance = _make_clock()
    wedged_gate = SessionOperationGate("wedged-one", "chromium", queue_timeout_seconds=30, clock=wedged_clock)
    healthy_gate = SessionOperationGate("healthy-one", "firefox", queue_timeout_seconds=30, clock=healthy_clock)

    wedged_entered = asyncio.Event()
    never = asyncio.Event()

    async def wedged_owner() -> None:
        async with wedged_gate.operation("wedged"):
            wedged_entered.set()
            await never.wait()

    healthy_entered = asyncio.Event()
    healthy_release = asyncio.Event()
    healthy_finished = False

    async def healthy_owner() -> None:
        nonlocal healthy_finished
        async with healthy_gate.operation("busy"):
            healthy_entered.set()
            await healthy_release.wait()
        healthy_finished = True

    wedged_task = asyncio.create_task(wedged_owner())
    healthy_task = asyncio.create_task(healthy_owner())
    async with asyncio.timeout(5):
        await wedged_entered.wait()
        await healthy_entered.wait()

    wedged_advance(100.0)  # far past the ceiling
    healthy_advance(5.0)  # comfortably inside it

    async with asyncio.timeout(5):
        assert await wedged_gate.enforce_active_timeout(60.0) is True
        assert await healthy_gate.enforce_active_timeout(60.0) is False

    async with asyncio.timeout(5):
        with pytest.raises(asyncio.CancelledError):
            await wedged_task
    assert wedged_gate.snapshot()["state"] == "broken"

    # The healthy session was never cancelled and finishes normally on its
    # own release, independent of the wedged session's fate.
    assert not healthy_task.done()
    healthy_release.set()
    async with asyncio.timeout(5):
        await healthy_task
    assert healthy_finished is True
    assert healthy_gate.snapshot()["state"] == "open"
