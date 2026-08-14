# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared test-fake base wiring a real ``SessionOperationGate`` behind the
same ``operation(...)``/``operation_snapshot()`` surface ``BrowserSession``
exposes.

Production code gets no fallback for an object without a gate, so any fake
session/pool/macro object that needs to look gate-aware to code under test
should inherit from ``OperationAwareFake`` rather than hand-rolling its own
stub. The gate here is real (not mocked) so admission/FIFO/close behavior in
tests reflects the actual state machine, not a guess at its shape.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import LiteralString

from octowright.session.operation_gate import (
    USE_DEFAULT,
    OperationGateSnapshot,
    SessionOperationGate,
    UseDefault,
)


class OperationAwareFake:
    """Base class for test fakes that need a real operation gate.

    Subclasses inherit ``operation(...)`` / ``operation_snapshot()`` for
    free; ``self._operation_gate`` is available for tests that need to drive
    the gate directly (e.g. ``reserve_close`` / ``mark_closed_external``).
    """

    def __init__(self) -> None:
        self._operation_gate = SessionOperationGate("fake-session", "chromium", queue_timeout_seconds=30)

    def operation(
        self,
        operation_name: LiteralString,
        *,
        wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT,
    ) -> AbstractAsyncContextManager[None]:
        return self._operation_gate.operation(operation_name, wait_timeout_seconds=wait_timeout_seconds)

    def operation_snapshot(self) -> OperationGateSnapshot:
        return self._operation_gate.snapshot()
