# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``SessionOperationGate`` and its public error/state surface.

Split into a package (``core.py`` the gate class + resolvers + the
``gated_operation`` decorator, ``types.py`` the dependency-free error/state
primitives, ``close.py`` the close-reservation mixin) purely to keep each
module under the repository's LOC ceiling -- ``operation_gate.py`` alone
would not fit Task 3's active-duration ceiling plus its hang-resilience
fix. This file holds ONLY re-exports and ``__all__``, never logic, so
existing imports (``from octowright.session.operation.gate import
SessionOperationGate``) keep working regardless of which submodule actually
defines a given name. A caller that needs a name deliberately left out of
``__all__`` (e.g. a test reaching for a private helper) imports it from the
owning submodule directly (``operation.gate.core`` / ``.types`` / ``.close``)
rather than growing this file's re-export surface.
"""

from __future__ import annotations

from octowright.session.operation.gate.core import (
    USE_DEFAULT,
    OperationGateSnapshot,
    SessionOperationGate,
    UseDefault,
    gated_operation,
    resolve_operation_active_timeout_seconds,
    resolve_operation_queue_timeout_seconds,
)
from octowright.session.operation.gate.types import (
    CloseReservation,
    OperationGateInvariantError,
    OperationGateState,
    SessionBusyTimeoutError,
    SessionCloseAbortedError,
    SessionClosedError,
    SessionClosingError,
    validate_operation_name,
)

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
    "SessionOperationGate",
    "UseDefault",
    "gated_operation",
    "resolve_operation_active_timeout_seconds",
    "resolve_operation_queue_timeout_seconds",
    "validate_operation_name",
]
