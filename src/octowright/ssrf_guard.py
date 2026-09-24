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
Every navigation's own URL is checked first -- including one the page started
itself (a link, a form, ``location = ...``), which no tool pre-flighted -- and
every hop is checked with :func:`ssrf.check_navigation_url_resolved`, so a
hostname is resolved and refused if any answer is non-public.

For a GET navigation the guard walks the chain itself with
``route.fetch(max_redirects=0)``, validating each ``Location`` **before**
fetching it, then hands the navigation back to the browser with
``route.fallback()`` once the whole chain is clear.

A non-GET navigation (in practice a form POST) cannot be fetched twice without
submitting it twice, so it is sent exactly **once**, by the guard, with
``route.fetch(max_redirects=0)``:

* not a redirect -- the response is fulfilled into the page as-is;
* ``303``, or ``301``/``302`` on a POST -- the browser would follow with a GET,
  so the guard validates the ``Location``, walks the rest of the chain with GET
  the same way the GET path does, and, if every hop is clean, fulfills the
  original route with the fetched 3xx so the browser follows it itself;
* ``307``/``308`` (or ``301``/``302`` on another method) -- method-preserving:
  the next hop would re-send the body, so it cannot be walked without
  re-submitting, and Playwright will not call this handler for it. The first
  ``Location`` is validated for the log line, and the navigation is aborted
  either way rather than letting unchecked later hops through.

Known costs, deliberately accepted (this only runs under an opt-in policy):

* **An allowed GET navigation is fetched twice** -- once to validate the
  chain, once by the browser. Letting the browser navigate for real is what
  keeps ``page.url``, the redirect history, and relative-URL resolution
  correct; fulfilling the final body against the original URL would silently
  break ``browser_expect_url`` and every relative link on the page. A POST
  that redirects has the same cost for the hops after the first.
* **A method-preserving redirect of a form submission is refused** under
  ``block-private``, even to a public host.
* **Validation and connection are separate DNS lookups.** The browser resolves
  a hop again when it connects, and Playwright cannot pin the validated
  address into that connection, so a rebinding DNS server can still answer
  differently the second time -- see ``ssrf``'s module docstring.
* Subresources are not checked at all: a fetch to a private host cannot be
  read back through the tool surface, and intercepting every image and XHR
  would break ordinary pages for no gain in this threat model.

With the default ``off`` policy nothing is registered, so none of this
touches a default deployment.
"""

from __future__ import annotations

import html
import json
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

#: The statuses a browser actually follows. Of these, 303 always becomes a
#: GET, and 301/302 become a GET for a POST (Fetch standard, "HTTP-redirect
#: fetch" step 12); 307/308 always re-send the method and body.
_FOLLOWED_REDIRECTS = frozenset({301, 302, 303, 307, 308})

#: Request headers that describe the submitted body, dropped when the chain
#: after a POST is walked with a bodiless GET.
_BODY_HEADERS = frozenset({"content-type", "content-length"})

#: Engines whose ``route.fulfill`` rejects a redirect status.
_NO_REDIRECT_FULFILL_ENGINES = frozenset({"webkit"})


class RedirectBlocked(ValueError):
    """A hop in the redirect chain is refused by the SSRF policy."""


async def _check_hop(url: str) -> None:
    """Refuse *url* under the policy, resolving its host."""
    try:
        await ssrf.check_navigation_url_resolved(url)
    except ValueError as exc:
        raise RedirectBlocked(str(exc)) from exc


async def _validate_chain(route: Any, start_url: str, *, as_get: dict[str, Any] | None = None) -> None:
    """Walk the redirect chain from *start_url*, refusing a blocked hop.

    Each ``Location`` is checked *before* the request that would fetch it, so
    a blocked host is never contacted. *as_get* carries the ``route.fetch``
    overrides that turn the intercepted request into a bodiless GET, for the
    chain after a POST was redirected; ``None`` replays the request as it is.
    """
    overrides = as_get or {}
    url = start_url
    for _ in range(MAX_REDIRECT_HOPS):
        response = await route.fetch(url=url, max_redirects=0, **overrides)
        if response.status not in _REDIRECT_STATUSES:
            return
        location = response.headers.get("location")
        if not location:
            return
        url = urljoin(url, location)
        await _check_hop(url)
    raise RedirectBlocked(f"redirect chain from {start_url!r} exceeded {MAX_REDIRECT_HOPS} hops")


def _client_redirect(target: str) -> str:
    """A document that replaces itself with *target*, with or without JavaScript."""
    attr = html.escape(target, quote=True)
    # json.dumps leaves "<" alone, so a "</script>" in the Location would close the tag.
    literal = json.dumps(target).replace("<", "\\u003c")
    refresh = f'<meta http-equiv="refresh" content="0;url={attr}">'
    return f"<!doctype html>{refresh}<script>location.replace({literal})</script>"


def _get_overrides(request: Any) -> dict[str, Any]:
    """``route.fetch`` overrides for a GET that carries none of *request*'s body.

    An empty ``post_data`` is what stops Playwright substituting the original
    request's body (it falls back to it whenever none is given).
    """
    headers = {k: v for k, v in (request.headers or {}).items() if k.lower() not in _BODY_HEADERS}
    return {"method": "GET", "headers": headers, "post_data": b""}


async def _handle_non_get(route: Any, request: Any, engine: str | None) -> None:
    """Send a non-GET navigation once, and only release a redirect it can vouch for."""
    try:
        response = await route.fetch(max_redirects=0)
    except Exception as exc:
        # The browser would have shown a network error; say so rather than
        # leaving the intercepted request unanswered.
        log.debug("octowright.ssrf.non_get_fetch_failed", url=request.url, error=repr(exc))
        await route.abort("failed")
        return
    location = response.headers.get("location")
    if response.status not in _FOLLOWED_REDIRECTS or not location:
        await route.fulfill(response=response)
        return
    target = urljoin(request.url, location)
    await _check_hop(target)
    becomes_get = response.status == 303 or (response.status in {301, 302} and request.method.upper() == "POST")
    if not becomes_get:
        raise RedirectBlocked(
            f"{response.status} redirect of a {request.method} navigation to {target!r} would re-send the "
            "request body to later hops this guard cannot see; refused under block-private"
        )
    await _validate_chain(route, target, as_get=_get_overrides(request))
    if engine in _NO_REDIRECT_FULFILL_ENGINES:
        # WebKit refuses a 3xx outright ("Route.fulfill: Cannot fulfill with
        # redirect status: 303"; measured on Playwright 1.62), and the refusal
        # consumes the route, so it cannot be tried and caught. The POST has
        # already been sent, so aborting would lose a submission the server
        # accepted. Hand the page a document that navigates to the validated
        # target instead: that is a NEW GET navigation, so it comes back
        # through this guard and is checked again.
        await route.fulfill(status=200, content_type="text/html", body=_client_redirect(target))
        return
    # The browser follows the 3xx itself -- and, per the module docstring,
    # without calling this handler again, which is why the chain was walked
    # first.
    await route.fulfill(response=response)


async def _handle_route(route: Any, request: Any, *, engine: str | None = None) -> None:
    """Abort a navigation whose redirect chain the policy refuses."""
    try:
        if not request.is_navigation_request():
            await route.fallback()
            return
        try:
            await _check_hop(request.url)
            if request.method.upper() != "GET":
                await _handle_non_get(route, request, engine)
                return
            await _validate_chain(route, request.url)
        except RedirectBlocked as exc:
            log.warning("octowright.ssrf.redirect_blocked", url=request.url, method=request.method, error=str(exc))
            await route.abort(_ABORT_REASON)
            return
        await route.fallback()
    except Exception as exc:  # pragma: no cover - route already gone
        # A route whose page navigated away raises on fallback and abort alike.
        # Swallowing keeps a dead route from surfacing as a launch failure.
        log.debug("octowright.ssrf.route_handler_failed", error=repr(exc))


async def install_navigation_guard(context: Any, *, engine: str | None = None) -> None:
    """Register the per-hop navigation check on *context*.

    No-op unless the SSRF policy is enabled, so the default deployment keeps
    an uninstrumented context. *engine* (``chromium``/``firefox``/``webkit``)
    picks how a validated POST redirect is released; when omitted it is read
    from the context's browser, which a persistent context does not have.
    """
    if not ssrf.policy_enabled():
        return
    if engine is None:
        browser = getattr(context, "browser", None)
        engine = getattr(getattr(browser, "browser_type", None), "name", None)

    async def handler(route: Any, request: Any) -> None:
        await _handle_route(route, request, engine=engine)

    await bounded(
        context.route("**/*", handler),
        operation="browser_install_navigation_guard",
    )
    log.debug("octowright.ssrf.navigation_guard_installed", engine=engine)
