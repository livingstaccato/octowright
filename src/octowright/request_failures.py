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

#: Resource types whose 4xx/5xx ``expect_network_clean(http_errors=True)``
#: counts: page loads and API calls, which are what "the journey worked" means.
#: A missing image, font or favicon is cosmetic and would make the check fail
#: on pages whose flow is fine.
HTTP_ERROR_RESOURCE_TYPES: frozenset[str] = frozenset({"document", "fetch", "xhr"})


def is_http_error(status: object, resource_type: object) -> bool:
    return isinstance(status, int) and status >= 400 and resource_type in HTTP_ERROR_RESOURCE_TYPES
