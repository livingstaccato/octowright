# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal-pool-specific exception types."""

from __future__ import annotations

from octowright.plugins.errors import ProtectedSessionCloseError


class ProtectedTerminalCloseError(ProtectedSessionCloseError, ValueError):
    """Raised when closing a protected terminal session requires force=True.

    Core's ``ProtectedSessionCloseError`` comes FIRST in the MRO because it is
    the contract: ``SessionPool.close`` promises that type, and
    ``http/routes/sessions._maybe_close_plugin`` catches only that type to map
    a refused close onto ``409`` with actionable "pass force=true" guidance.
    Raising a type outside that hierarchy is not a cosmetic difference — the
    route's ``except`` does not match, nothing above it catches, and Starlette
    turns the refusal into a generic ``500``.

    ``ValueError`` is kept in the MRO deliberately: ``terminal_launch``'s broad
    ``except ValueError`` and any external caller that has been catching this
    as one keep working, so adding the contract type takes nothing away.
    """


class TerminalDisconnectedError(RuntimeError):
    """Raised by ``TerminalEngine.send_input`` when the connector is no longer
    connected, so the input was NOT delivered. The ``terminal_send_input`` tool
    maps it to ``{"ok": False, "error": ...}`` instead of falsely reporting
    success — a distinct type lets callers catch delivery failure specifically.
    """


class TerminalPoolUnavailableError(RuntimeError):
    """Raised when terminal code is reached without a wired ``terminal_pool``.

    These call sites are guarded by upstream invariants (the terminal tools only
    register when the pool exists; ``ScenarioPool.start`` only produces terminal
    specs once the pool is built), so this should never fire in practice. It
    exists so the guard is a real runtime check that survives ``python -O``
    (which strips ``assert``) instead of degrading into a ``NoneType`` crash.
    """
