# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.engines import playwright_failure_sanity


class ProtectedBrowserCloseError(ValueError):
    """Raised when a protected browser close requires an explicit force flag."""


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
