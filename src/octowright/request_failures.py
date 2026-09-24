# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Which Playwright ``requestfailed`` texts mean "cancelled" rather than "failed".

A package-root module, like ``console_levels``, so the session and the exported
macro CLI (which must not import the browser stack) share one list.
"""

from __future__ import annotations

#: A cancelled request is not the page failing: navigating away aborts what is
#: in flight, and apps cancel their own fetches. Measured on Playwright 1.62,
#: Linux, with an AbortController-cancelled fetch: Chromium ``net::ERR_ABORTED``,
#: Firefox ``NS_BINDING_ABORTED``, WebKit ``Load request cancelled`` (the same
#: two Firefox/WebKit spellings for navigating away; Chromium fires no
#: ``requestfailed`` there). ``cancelled`` is NOT measured: it is macOS
#: CFNetwork's NSURLErrorCancelled description, which WebKit on macOS reports
#: through a different network stack than the Linux one measured here.
ABORTED_REQUEST_FAILURES: frozenset[str] = frozenset(
    {"net::ERR_ABORTED", "NS_BINDING_ABORTED", "Load request cancelled", "cancelled"}
)

#: Resource types whose 4xx/5xx ``expect_network_clean(http_errors=True)``
#: counts: page loads and API calls, which are what "the journey worked" means.
#: A missing image, font or favicon is cosmetic and would make the check fail
#: on pages whose flow is fine.
HTTP_ERROR_RESOURCE_TYPES: frozenset[str] = frozenset({"document", "fetch", "xhr"})


#: Resource types a settle wait must not wait for: they stay open by design.
LONG_LIVED_RESOURCE_TYPES: frozenset[str] = frozenset({"eventsource", "websocket", "media"})


def is_http_error(status: object, resource_type: object) -> bool:
    return isinstance(status, int) and status >= 400 and resource_type in HTTP_ERROR_RESOURCE_TYPES
