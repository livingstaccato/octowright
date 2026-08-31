# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Re-check every navigation hop against the SSRF policy, not just the first.

``ssrf.check_navigation_url`` runs pre-flight, on the URL an MCP tool or a
replayed macro asked for. A redirect is not that URL: a public page that
answers ``302 Location: http://169.254.169.254/...`` reaches the metadata
service with the guard none the wiser, and the read tools then hand the
response back to the model. Verified against a real Chromium -- an allowed
first hop landed on a loopback target and its body was readable.

Why the obvious implementation does not work
--------------------------------------------
Playwright does **not** re-invoke a route handler for a redirected request.
Measured both ways: after ``route.fallback()`` *and* after
``route.fulfill(response=<the 302>)``, Chromium follows the chain inside the
network stack and the handler is called exactly once, for the first hop, while
the server sees every hop. So a handler that merely inspects ``request.url``
is a no-op on precisely the case it exists for.

What this does instead
----------------------
For a GET navigation the guard walks the chain itself with
``route.fetch(max_redirects=0)``, validating each ``Location`` **before**
fetching it, then hands the navigation back to the browser with
``route.fallback()`` once the whole chain is clear.

Known costs, deliberately accepted (this only runs under an opt-in policy):

* **An allowed GET navigation is fetched twice** -- once to validate the
  chain, once by the browser. Letting the browser navigate for real is what
  keeps ``page.url``, the redirect history, and relative-URL resolution
  correct; fulfilling the final body against the original URL would silently
  break ``browser_expect_url`` and every relative link on the page.
* **Non-GET navigations are not chain-checked.** Replaying a POST to
  validate it would double-submit the form. They keep the pre-flight check
  only, and the gap is logged.
* Subresources are not checked at all: a fetch to a private host cannot be
  read back through the tool surface, and intercepting every image and XHR
  would break ordinary pages for no gain in this threat model.

With the default ``off`` policy nothing is registered, so none of this
touches a default deployment.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from provide.telemetry import get_logger

from octowright import ssrf
from octowright.session.timeouts import bounded

log = get_logger(__name__)

# Chromium surfaces this as ERR_BLOCKED_BY_CLIENT, which reads correctly in
# the page and in the network log.
_ABORT_REASON = "blockedbyclient"

# Matches the hop limit browsers enforce; a chain longer than this is broken
# anyway, and the bound keeps a redirect loop from spinning the validator.
MAX_REDIRECT_HOPS = 20

_REDIRECT_STATUSES = range(300, 400)


class RedirectBlocked(ValueError):
    """A hop in the redirect chain is refused by the SSRF policy."""


async def _validate_chain(route: Any, start_url: str) -> None:
    """Walk the redirect chain from *start_url*, refusing a blocked hop.

    Each ``Location`` is checked *before* the request that would fetch it, so
    a blocked host is never contacted.
    """
    url = start_url
    for _ in range(MAX_REDIRECT_HOPS):
        response = await route.fetch(url=url, max_redirects=0)
        if response.status not in _REDIRECT_STATUSES:
            return
        location = response.headers.get("location")
        if not location:
            return
        url = urljoin(url, location)
        try:
            ssrf.check_navigation_url(url)
        except ValueError as exc:
            raise RedirectBlocked(str(exc)) from exc
    raise RedirectBlocked(f"redirect chain from {start_url!r} exceeded {MAX_REDIRECT_HOPS} hops")


async def _handle_route(route: Any, request: Any) -> None:
    """Abort a navigation whose redirect chain the policy refuses."""
    try:
        if not request.is_navigation_request():
            await route.fallback()
            return
        if request.method.upper() != "GET":
            # Chain-checking would mean replaying the submission.
            log.debug("octowright.ssrf.chain_check_skipped", method=request.method, url=request.url)
            await route.fallback()
            return
        try:
            await _validate_chain(route, request.url)
        except RedirectBlocked as exc:
            log.warning("octowright.ssrf.redirect_blocked", url=request.url, error=str(exc))
            await route.abort(_ABORT_REASON)
            return
        await route.fallback()
    except Exception as exc:  # pragma: no cover - route already gone
        # A route whose page navigated away raises on fallback and abort alike.
        # Swallowing keeps a dead route from surfacing as a launch failure.
        log.debug("octowright.ssrf.route_handler_failed", error=repr(exc))


async def install_navigation_guard(context: Any) -> None:
    """Register the per-hop navigation check on *context*.

    No-op unless the SSRF policy is enabled, so the default deployment keeps
    an uninstrumented context.
    """
    if not ssrf.policy_enabled():
        return
    await bounded(
        context.route("**/*", _handle_route),
        operation="browser_install_navigation_guard",
    )
    log.debug("octowright.ssrf.navigation_guard_installed")
