# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal-pool-specific exception types."""

from __future__ import annotations


class ProtectedTerminalCloseError(ValueError):
    """Raised when closing a protected terminal session requires force=True."""
