# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.engines import playwright_failure_sanity


class ProtectedBrowserCloseError(ValueError):
    """Raised when a protected browser close requires an explicit force flag."""


class BrowserCapExceededError(RuntimeError):
    """Raised when a user-facing launch would exceed OCTOWRIGHT_MAX_BROWSERS.

    The cap is pool-wide (shared across every MCP client on this leader), so the
    message tells the caller to close browsers or raise the cap rather than
    implying the launch itself was malformed.
    """


class MemoryPressureError(RuntimeError):
    """Raised when a user-facing launch is refused because available memory is
    below the OCTOWRIGHT_MIN_FREE_MEMORY_MB floor.

    Opt-in (the floor is unset by default), so this fires only when an operator
    has configured the guard. The message tells the caller to free memory or
    disable the guard rather than implying the launch was malformed.
    """


def maybe_wrap_playwright_error(exc: Exception, *, kind: str) -> Exception:
    hint = playwright_failure_sanity(str(exc), kind=kind)
    if hint is None:
        return exc
    return RuntimeError(
        f"{exc}\n\n[octowright_sanity]\n"
        f"category={hint['category']}\n"
        f"probable_cause={hint['probable_cause']}\n"
        f"recommended_actions={hint['recommended_actions']}"
    )
