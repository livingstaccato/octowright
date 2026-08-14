# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Dependency-free primitives shared by ``operation_gate.py`` and ``operation_gate_close.py``.

Mirrors ``session/_constants.py``/``session/_protocols.py``: a third module
with no dependency on either consumer, so mixin-style modules can share these
types without forming an import cycle (see ``session/core.py``'s
``_constants`` comment for the same rationale applied to session mixins).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

import anyio

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


def validate_operation_name(name: str) -> str:
    if not _OPERATION_NAME_RE.fullmatch(name):
        raise ValueError(f"operation name must be a fixed identifier, got {name!r}")
    return name


_T = TypeVar("_T")


async def _join_after_cancellation(task: asyncio.Task[_T]) -> _T:
    """Join ``task`` despite the joining task being cancelled again mid-join.

    Local re-implementation of ``session_manifest.wait_task_after_cancellation``:
    duplicated rather than imported so this session-layer primitive never
    depends on the higher-level manifest module built on top of it. The
    ``anyio.CancelScope(shield=True)`` matters even though the outer
    ``asyncio.shield`` already protects ``task`` from being cancelled itself:
    it protects *this join* from anyio-level re-cancellation (the MCP server
    runs under anyio, whose cancel scopes keep re-delivering ``CancelledError``
    at every checkpoint until their ``with`` block exits) so the loop below
    reliably reaches ``task.done()`` instead of degrading into a spin that
    merely re-arms on each iteration.
    """
    current = asyncio.current_task()
    while not task.done():
        try:
            with anyio.CancelScope(shield=True):
                await asyncio.shield(task)
        except asyncio.CancelledError:
            if current is not None:
                current.uncancel()
    return task.result()


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


def _observe_future_exception(future: asyncio.Future[object]) -> None:
    # A CloseReservation.outcome may end up with no remaining observer (every
    # caller cancelled .wait(), which shields the underlying future). Calling
    # .exception() here -- without consuming it -- stops asyncio's "exception
    # was never retrieved" warning; a later await of the same future still
    # raises the identical stored object.
    def _observe(done: asyncio.Future[object]) -> None:
        if not done.cancelled():
            done.exception()

    future.add_done_callback(_observe)


@dataclass(slots=True)
class _Waiter:
    task: asyncio.Task[object] | None
    operation_name: str
    queued_at: float
    ready: asyncio.Future[None]
    granted: bool = False


@dataclass(frozen=True, slots=True)
class _LeaseToken:
    owner_task: asyncio.Task[object]
    operation_name: str


@dataclass(slots=True)
class CloseReservation:
    operation_name: str
    waiter: _Waiter
    outcome: asyncio.Future[object]
    teardown_only: bool = False

    async def wait(self) -> object:
        return await asyncio.shield(self.outcome)
