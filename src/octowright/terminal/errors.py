# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal-pool-specific exception types."""

from __future__ import annotations


class ProtectedTerminalCloseError(ValueError):
    """Raised when closing a protected terminal session requires force=True."""


class TerminalPoolUnavailableError(RuntimeError):
    """Raised when terminal code is reached without a wired ``terminal_pool``.

    These call sites are guarded by upstream invariants (the terminal tools only
    register when the pool exists; ``ScenarioPool.start`` only produces terminal
    specs once the pool is built), so this should never fire in practice. It
    exists so the guard is a real runtime check that survives ``python -O``
    (which strips ``assert``) instead of degrading into a ``NoneType`` crash.
    """
