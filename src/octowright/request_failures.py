# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Which Playwright ``requestfailed`` texts mean "cancelled" rather than "failed".

A package-root module, like ``console_levels``, so the session and the exported
macro CLI (which must not import the browser stack) share one list.
"""

from __future__ import annotations

#: Navigating away aborts in-flight requests; that is not the page failing.
#: Measured on Playwright 1.62 by navigating away from a pending fetch: Firefox
#: says ``NS_BINDING_ABORTED`` and WebKit ``Load request cancelled``; Chromium
#: fired no ``requestfailed`` there, and reports its other cancellations as
#: ``net::ERR_ABORTED``.
ABORTED_REQUEST_FAILURES: frozenset[str] = frozenset(
    {"net::ERR_ABORTED", "NS_BINDING_ABORTED", "Load request cancelled"}
)
