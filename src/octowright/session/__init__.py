# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""BrowserSession + per-feature helper modules.

Public API: ``BrowserSession`` and ``DEFAULT_PREVIEW_CHARS`` are re-exported
from this package so existing imports (`from octowright.session import
BrowserSession`) continue to work after the split. The operation-gate error
types, state enum, and snapshot shape are re-exported too, since every
``BrowserSession`` now owns a gate (see ``operation_gate.py``) and callers
outside this package need to catch/inspect them without reaching past the
package boundary into the gate's own module.
"""

from __future__ import annotations

from octowright.session.core import DEFAULT_PREVIEW_CHARS, BrowserSession
from octowright.session.operation_gate import (
    OperationGateInvariantError,
    OperationGateSnapshot,
    OperationGateState,
    SessionBusyTimeoutError,
    SessionClosedError,
    SessionClosingError,
)

__all__ = [
    "DEFAULT_PREVIEW_CHARS",
    "BrowserSession",
    "OperationGateInvariantError",
    "OperationGateSnapshot",
    "OperationGateState",
    "SessionBusyTimeoutError",
    "SessionClosedError",
    "SessionClosingError",
]
