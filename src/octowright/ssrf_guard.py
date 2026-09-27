# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Check every request URL against the SSRF policy, and serve the browser what was checked.

``ssrf.check_navigation_url`` runs pre-flight, on the URL an MCP tool or a
replayed macro asked for. A redirect is not that URL: a public page that
answers ``302 Location: http://169.254.169.254/...`` reaches the metadata
service with the guard none the wiser, and the read tools then hand the
response back to the model. Verified against a real Chromium -- an allowed
first hop landed on a loopback target and its body was readable.

Why the obvious implementation does not work
--------------------------------------------
Playwright does **not** re-invoke a route handler for a redirected request.
Measured on chromium, firefox and webkit (Playwright 1.62): after
``route.fallback()`` *and* after ``route.fulfill(response=<the 302>)``, the
engine follows the chain inside its network stack and the handler is called
exactly once, for the first hop, while the server sees every hop. So neither a
handler that inspects ``request.url`` nor one that answers hop by hop with the
validated 3xx sees the hops after the first. (WebKit also refuses a 3xx in
``route.fulfill`` outright.)

Why the browser is never allowed to fetch a navigation itself
-------------------------------------------------------------
This guard used to validate the chain with ``route.fetch`` and then
``route.fallback()``, so the browser fetched every hop again. A server that
answers the first request "200" and the second "302 -> private" -- or does so
at any hop of a chain -- had its redirect followed unchecked (measured on all
three engines). Validating one response and delivering another is the whole
bug, so each navigation is now fetched **exactly once, by the guard**
(``route.fetch(max_redirects=0)``), and:

* **not a redirect** -- the fetched response is fulfilled into the page as-is.
  That is the common case, and it costs nothing: ``page.url``, the status, and
  relative-URL resolution are those of the one URL requested.
* **a redirect** -- the ``Location`` is checked, and the page is handed a tiny
  document that replaces itself with it (``location.replace`` plus a meta
  refresh). That starts a NEW navigation, which comes back through this
  handler and is fetched and checked the same way, so the browser only ever
  receives responses the guard validated. Why not fulfil the whole chain's
  final body against the first URL: ``page.url`` would stay the first URL,
  breaking ``browser_expect_url`` and every relative link; and a cross-origin
  final body would run under the FIRST URL's origin. The client redirect lands
  on the real final URL (measured: ``page.url`` and ``<a href="rel">`` resolve
  against it on all three engines). Cookies set by an intermediate hop still
  land -- ``route.fetch`` shares the context's cookie jar (measured).

A non-GET navigation (in practice a form POST) is fetched once the same way.
A ``303``, or ``301``/``302`` on a POST, becomes a GET, so it is answered with
the same client redirect to the validated ``Location``. A ``307``/``308`` (or
``301``/``302`` on another method) would re-send the body to a hop this guard
could only validate by submitting it again, so it is refused.

A redirect ``Location`` that is not http(s) is refused before any document is
built, as a browser refuses it: the policy has no host to check for
``javascript:``, and served as a client redirect it ran as script in the
redirecting URL's origin (measured on all three engines).

Every other request -- images, scripts, ``fetch``/XHR, WebSockets -- has its
URL checked too, against :func:`ssrf.check_request_url_cached`: a literal
non-public IP or a refused name is aborted, and a hostname is resolved (off the
event loop, one lookup per host per TTL) and aborted if any answer is
non-public. WebSockets are not visible to ``context.route`` at all; they are
routed separately with ``route_web_socket`` and either closed or connected
through.

What is NOT checked: a subresource's redirect
----------------------------------------------
Only a subresource's first URL is checked. The handler is never called again
for its redirect hops (measured on all three engines, after ``fallback()`` and
``continue_()`` alike), so ``<img src=https://public.example/r>`` or a
``no-cors`` ``fetch`` answering ``302 -> http://169.254.169.254/...`` reaches
the private host on **firefox and webkit** -- a blind SSRF: the page cannot
read the response, but the request is made. **Chromium** refuses it today,
and not because of this guard: its Local Network Access check treats a
guard-fulfilled page as public and headless denies the permission. That is a
browser feature this guard neither controls nor relies on.

It is not closed, because the only way to see the hops is for the guard to
fetch every subresource itself (``route.fetch(max_redirects=0)``, walking the
chain) and fulfil the final response -- and a redirect cannot be known before
the fetch, so that cost lands on every request. Prototyped and measured on all
three engines, it breaks what the browser enforces for the page:

* a same-origin URL redirecting cross-origin became **readable** by the page
  (the final body is fulfilled against the first URL, so the CORS check the
  browser applies after a cross-origin redirect never happens);
* a streamed response (``text/event-stream``, a ``ReadableStream`` body)
  delivered **nothing** until it ended -- ``route.fetch`` buffers the body;
* ``fetch(url, {credentials: 'omit'})`` **still sent the context's cookies**.

Range requests, the HTTP cache and large media would all pay the same
buffering. A deployment that must stop a subresource redirect needs
network-layer egress control, as for DNS rebinding below.

Known costs, deliberately accepted (this only runs under an opt-in policy):

* **A redirecting navigation's ``goto`` returns the client-redirect document's
  synthetic 200**, not the 3xx chain, and ``response.request.redirected_from``
  is empty. ``page.url`` and the page itself are the final ones: the document
  replaces itself while still parsing, so its ``load`` never fires and
  ``goto`` resolves on the destination (measured on all three engines). The
  real chain is kept on the session's network rows instead: each hop's row
  carries its real 3xx ``status``, ``redirect_location`` and
  ``served_as: "client_redirect"`` (``client_redirect_of``).
* **A refused later hop is not always an error to the browser.** Chromium's
  ``goto`` resolves on the error page, and a popup on firefox and webkit sits
  on the client-redirect document with no further event. The tool paths
  therefore read the guard's own verdict (:class:`FrameChain`,
  :func:`begin_navigation`, :func:`raise_if_refused`): ``navigate``,
  ``navigate_back``, launch navigation and ``open_url`` raise or report the
  refusal, and ``open_url``'s popup waits past the client-redirect document
  (``CLIENT_REDIRECT_MARKER``) to the destination's ``load``.
* **A method-preserving redirect of a form submission is refused** under
  ``block-private``, even to a public host.
* **Every WebSocket message is relayed through the Playwright driver** once a
  socket is routed.
* **Validation and connection are still separate DNS lookups for
  subresources**, and the browser resolves them itself. A navigation is
  fetched by the guard, but Playwright's fetch resolves too; neither can be
  pinned to the validated address -- see ``ssrf``'s module docstring.

With the default ``off`` policy nothing is registered, so none of this
touches a default deployment.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import re
import weakref
from collections.abc import Awaitable
from typing import Any
from urllib.parse import urljoin

from provide.telemetry import get_logger

from octowright import ssrf
from octowright.request_errors import InvalidRequestError
from octowright.session.timeouts import bounded

log = get_logger(__name__)

# Chromium surfaces this as ERR_BLOCKED_BY_CLIENT, which reads correctly in
# the page and in the network log.
_ABORT_REASON = "blockedbyclient"
# Not blockedbyclient for subresources: WebKit leaves an <img> aborted with it
# pending forever -- neither load nor error fires (measured, Playwright 1.62),
# so a page waiting on it hangs. accessdenied errors promptly on all three.
_SUBRESOURCE_ABORT_REASON = "accessdenied"

# Matches the hop limit browsers enforce; a chain longer than this is broken
# anyway, and the bound keeps a redirect loop from spinning forever -- each hop
# is its own navigation here, so the browser's own limit never applies.
MAX_REDIRECT_HOPS = 20

#: The statuses a browser actually follows. Of these, 303 always becomes a
#: GET, and 301/302 become a GET for a POST (Fetch standard, "HTTP-redirect
#: fetch" step 12); 307/308 always re-send the method and body.
_FOLLOWED_REDIRECTS = frozenset({301, 302, 303, 307, 308})

_EVERY_URL = re.compile(".*")

#: The only schemes a redirect may lead to. A browser refuses a 3xx to any
#: other scheme as a network error (measured: ERR_UNSAFE_REDIRECT on chromium,
#: "not HTTP(S)" on webkit, NS_ERROR_CORRUPTED_CONTENT on firefox), and the
#: policy has no host to check for ``javascript:`` -- served as a client
#: redirect, it ran as script in the redirecting URL's origin on all three.
_REDIRECT_SCHEMES = frozenset({"http", "https"})


class RedirectBlocked(ValueError):
    """A hop in the redirect chain is refused by the SSRF policy."""


async def _check_hop(url: str) -> None:
    """Refuse *url* under the policy, resolving its host."""
    try:
        await ssrf.check_navigation_url_resolved(url)
    except ValueError as exc:
        raise RedirectBlocked(str(exc)) from exc


#: Names the client-redirect document, so a caller that only sees load states
#: can tell it is not the destination: a popup's first request has no frame to
#: record that on (see ``_HopCounter._frame``).
CLIENT_REDIRECT_MARKER = "octowright-client-redirect"
IS_CLIENT_REDIRECT_JS = f"() => !!document.querySelector('meta[name=\"{CLIENT_REDIRECT_MARKER}\"]')"


def _client_redirect(target: str) -> str:
    """A document that replaces itself with *target*, with or without JavaScript."""
    attr = html.escape(target, quote=True)
    # json.dumps leaves "<" alone, so a "</script>" in the Location would close the tag.
    literal = json.dumps(target).replace("<", "\\u003c")
    refresh = f'<meta http-equiv="refresh" content="0;url={attr}">'
    marker = f'<meta name="{CLIENT_REDIRECT_MARKER}">'
    return f"<!doctype html>{marker}{refresh}<script>location.replace({literal})</script>"


#: The redirect each client-redirected navigation really got, keyed by its
#: request. The browser saw a 200; the session's ``response`` listener reads
#: this so the recorded row keeps the real chain (``client_redirect_of``).
_CLIENT_REDIRECTS: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()


def client_redirect_of(request: Any) -> dict[str, Any] | None:
    """``{status, status_text, location}`` of the redirect the guard answered *request* for, else None."""
    try:
        return _CLIENT_REDIRECTS.get(request)
    except TypeError:  # a request double that cannot be weakly referenced
        return None


class FrameChain:
    """Whether the guard refused a navigation in one frame, for a caller that cannot see it.

    A caller waiting on the browser alone cannot always tell. On chromium a
    ``goto`` whose LATER hop was refused -- the client-redirect document's own
    navigation, not the ``goto``'s -- resolves on the error page instead of
    raising; on firefox and webkit that hop leaves the client-redirect document
    in place and fires no event at all, so a popup waiting for a load state
    waits out its whole timeout (all measured).
    """

    __slots__ = ("reason", "refused")

    def __init__(self) -> None:
        self.refused = asyncio.Event()
        self.reason: str | None = None


_FRAME_CHAINS: weakref.WeakKeyDictionary[Any, FrameChain] = weakref.WeakKeyDictionary()


def frame_chain(frame: Any) -> FrameChain:
    """The guard's record for *frame*, created empty on first use."""
    chain = _FRAME_CHAINS.get(frame)
    if chain is None:
        chain = _FRAME_CHAINS[frame] = FrameChain()
    return chain


def begin_navigation(frame: Any) -> FrameChain | None:
    """A fresh record for a navigation about to start in *frame*; ``None`` with the policy off.

    Pair it with :func:`raise_if_refused` after the ``goto``: on chromium a
    ``goto`` whose LATER hop the guard refused resolves on the error page
    instead of raising (measured), so it would report success.
    """
    if not ssrf.policy_enabled():
        return None
    chain = _FRAME_CHAINS[frame] = FrameChain()
    return chain


def raise_if_refused(chain: FrameChain | None) -> None:
    """Raise the refusal the guard recorded on *chain* since :func:`begin_navigation`, if any."""
    if chain is not None and chain.refused.is_set():
        raise InvalidRequestError(chain.reason or "navigation refused by the SSRF policy")


async def until_refused(wait: Awaitable[Any], chain: FrameChain) -> str | None:
    """Await *wait* unless the guard refuses a navigation in *chain*'s frame first.

    Returns the refusal, or ``None`` once *wait* finished. A refusal recorded
    before the call wins at once -- the frame is fresh, so it is this one's.
    """
    if chain.refused.is_set():
        _discard(wait)
        return chain.reason
    waiting = asyncio.ensure_future(wait)
    refused = asyncio.ensure_future(chain.refused.wait())
    try:
        await asyncio.wait({waiting, refused}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (waiting, refused):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
    if chain.refused.is_set():
        return chain.reason
    waiting.result()
    return None


def _discard(wait: Awaitable[Any]) -> None:
    """Close a coroutine that will not be awaited, so it is not reported as never awaited."""
    close = getattr(wait, "close", None)
    if close is not None:
        close()


class _HopCounter:
    """Consecutive guard-issued redirects per frame.

    Each hop of a chain is its own navigation now, so the browser's own
    redirect limit never applies and ``/loop -> /loop`` would spin forever.
    Reset whenever a frame's chain ends: a real (non-redirect) response is
    served, or a hop is refused or fails. An abort that left its count behind
    would start the frame's next, unrelated chain partway to the limit.
    """

    def __init__(self) -> None:
        self._hops: weakref.WeakKeyDictionary[Any, int] = weakref.WeakKeyDictionary()

    @staticmethod
    def _frame(request: Any) -> Any:
        try:
            return request.frame
        except Exception as exc:
            # A service-worker request has no frame, and nor does a popup's
            # first navigation (measured: Playwright raises until the frame
            # exists). Neither is bounded by the hop limit or recorded on a
            # FrameChain; say so rather than dropping both silently.
            log.debug("octowright.ssrf.request_without_frame", error=repr(exc))
            return None

    def step(self, request: Any, target: str) -> None:
        frame = self._frame(request)
        if frame is None:
            return
        hops = self._hops.get(frame, 0) + 1
        if hops > MAX_REDIRECT_HOPS:
            self._hops.pop(frame, None)
            raise RedirectBlocked(f"redirect chain reaching {target!r} exceeded {MAX_REDIRECT_HOPS} hops")
        self._hops[frame] = hops

    def reset(self, request: Any, refusal: str | None = None) -> None:
        """End *request*'s frame's chain; *refusal* says the navigation was refused or failed."""
        frame = self._frame(request)
        if frame is None:
            return
        self._hops.pop(frame, None)
        if refusal is not None:
            chain = frame_chain(frame)
            chain.reason = refusal
            chain.refused.set()


async def _serve_navigation(route: Any, request: Any, hops: _HopCounter) -> None:
    """Fetch a navigation once and hand the page only what was validated."""
    try:
        response = await route.fetch(max_redirects=0)
    except Exception as exc:
        # The browser would have shown a network error; say so rather than
        # leaving the intercepted request unanswered.
        log.debug("octowright.ssrf.navigation_fetch_failed", url=request.url, error=repr(exc))
        hops.reset(request, refusal=f"navigation to {request.url!r} failed: {exc}")
        await route.abort("failed")
        return
    location = response.headers.get("location")
    if response.status not in _FOLLOWED_REDIRECTS or not location:
        hops.reset(request)
        await route.fulfill(response=response)
        return
    target = urljoin(request.url, location)
    scheme = target.partition(":")[0].strip().lower()
    if scheme not in _REDIRECT_SCHEMES:
        raise RedirectBlocked(f"redirect to a {scheme!r} URL refused; only http(s) redirects are followed")
    await _check_hop(target)
    method = request.method.upper()
    becomes_get = method == "GET" or response.status == 303 or (response.status in {301, 302} and method == "POST")
    if not becomes_get:
        raise RedirectBlocked(
            f"{response.status} redirect of a {request.method} navigation to {target!r} would re-send the "
            "request body to a hop this guard could only check by submitting it again; refused under block-private"
        )
    hops.step(request, target)
    _CLIENT_REDIRECTS[request] = {"status": response.status, "status_text": response.status_text, "location": target}
    # A NEW navigation, which comes back through this handler -- see the
    # module docstring for why the 3xx itself is never handed to the browser.
    await route.fulfill(status=200, content_type="text/html", body=_client_redirect(target))


async def _handle_subresource(route: Any, request: Any) -> None:
    try:
        await ssrf.check_request_url_cached(request.url)
    except ValueError as exc:
        log.debug("octowright.ssrf.subresource_blocked", url=request.url, error=str(exc))
        await route.abort(_SUBRESOURCE_ABORT_REASON)
        return
    await route.fallback()


async def _handle_route(route: Any, request: Any, hops: _HopCounter) -> None:
    """Abort a request the policy refuses; serve a navigation from its own validated fetch."""
    try:
        if not request.is_navigation_request():
            await _handle_subresource(route, request)
            return
        try:
            await _check_hop(request.url)
            await _serve_navigation(route, request, hops)
        except RedirectBlocked as exc:
            log.warning("octowright.ssrf.redirect_blocked", url=request.url, method=request.method, error=str(exc))
            hops.reset(request, refusal=str(exc))
            await route.abort(_ABORT_REASON)
    except Exception as exc:  # pragma: no cover - route already gone
        # A route whose page navigated away raises on fulfill and abort alike.
        # Swallowing keeps a dead route from surfacing as a launch failure.
        log.debug("octowright.ssrf.route_handler_failed", error=repr(exc))


def _as_http(url: str) -> str:
    """The http(s) URL a ws(s) URL's host is checked as."""
    scheme, sep, rest = url.partition(":")
    return {"ws": "http", "wss": "https"}.get(scheme.lower(), scheme) + sep + rest


async def _handle_websocket(ws: Any) -> None:
    try:
        await ssrf.check_request_url_cached(_as_http(ws.url))
    except ValueError as exc:
        log.debug("octowright.ssrf.websocket_blocked", url=ws.url, error=str(exc))
        await ws.close()
        return
    # Routing a socket detaches it from the server until this is called;
    # messages are then relayed both ways unchanged.
    ws.connect_to_server()


async def install_navigation_guard(context: Any) -> None:
    """Register the per-request check on *context*.

    No-op unless the SSRF policy is enabled, so the default deployment keeps
    an uninstrumented context.
    """
    if not ssrf.policy_enabled():
        return
    hops = _HopCounter()

    async def handler(route: Any, request: Any) -> None:
        await _handle_route(route, request, hops)

    await bounded(
        context.route("**/*", handler),
        operation="browser_install_navigation_guard",
    )
    route_web_socket = getattr(context, "route_web_socket", None)
    if route_web_socket is not None:
        # A glob does not match ws:// URLs (measured); a pattern that matches
        # everything does.
        await bounded(
            route_web_socket(_EVERY_URL, _handle_websocket),
            operation="browser_install_websocket_guard",
        )
    log.debug("octowright.ssrf.navigation_guard_installed")
