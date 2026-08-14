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

This shape is locked by the design plan (Task 4) and referenced verbatim by
later tasks' test code (e.g. Task 9 reaches ``session._test_operation_gate``
directly) -- do not rename ``_test_operation_gate`` or drop the overridable
``instance_id``/``kind`` class attributes without updating every consumer.
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
    free; ``self._test_operation_gate`` is available for tests that need to
    drive the gate directly (e.g. ``reserve_close`` / ``mark_closed_external``).
    ``instance_id``/``kind`` are class attributes (not hardcoded in
    ``__init__``) so a subclass can override them to feed a different
    session id/kind into its gate.
    """

    instance_id = "fake-session"
    kind = "chromium"

    def __init__(self) -> None:
        self._test_operation_gate = SessionOperationGate(
            self.instance_id,
            self.kind,
            queue_timeout_seconds=30,
        )

    def operation(
        self,
        operation_name: LiteralString,
        *,
        wait_timeout_seconds: float | None | UseDefault = USE_DEFAULT,
    ) -> AbstractAsyncContextManager[None]:
        return self._test_operation_gate.operation(
            operation_name,
            wait_timeout_seconds=wait_timeout_seconds,
        )

    def operation_snapshot(self) -> OperationGateSnapshot:
        return self._test_operation_gate.snapshot()
