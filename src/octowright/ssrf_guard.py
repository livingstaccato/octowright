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
  ``goto`` resolves on the error page, and on firefox and webkit a ``goto`` or
  a popup sits on the client-redirect document with no further event. So the
  guard records how it ended each frame's navigation (:class:`FrameChain`:
  refused by the policy, or its fetch failed) on the record the frame had when
  the guard started on that request -- a tool navigation begins a fresh one,
  so a late ending of the navigation it replaced is dropped rather than read
  as its own -- and every tool navigation --
  ``navigate``, ``navigate_back``, launch, the new-tab redirect, ``open_url``
  and crash recovery -- goes through :func:`guarded_navigation`, which ends the
  wait on that verdict and raises it. A popup's first request has no frame yet,
  so its ending is parked until the popup page exists (``_UNFRAMED``).
  ``open_url``'s popup, which only has load states to wait on, waits past the
  client-redirect document (``CLIENT_REDIRECT_MARKER``) to the destination's
  ``domcontentloaded`` -- or to the popup closing itself, which fires none.
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
import itertools
import json
import re
import weakref
from collections.abc import Awaitable
from typing import Any, TypeVar, cast
from urllib.parse import urljoin

from provide.telemetry import get_logger

from octowright import ssrf
from octowright.request_errors import InvalidRequestError
from octowright.session.timeouts import bounded

log = get_logger(__name__)

T = TypeVar("T")

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


class NavigationFailedError(RuntimeError):
    """The guard's own fetch of a navigation failed (DNS, reset, TLS): a network error, not a refusal."""


async def _check_hop(url: str) -> None:
    """Refuse *url* under the policy, resolving its host."""
    try:
        await ssrf.check_navigation_url_resolved(url)
    except ValueError as exc:
        raise RedirectBlocked(str(exc)) from exc


#: Names the client-redirect document, so a caller that only sees load states
#: can tell it is not the destination: its ``domcontentloaded`` fires like any
#: page's, and nothing else about it says "not there yet".
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


#: What the guard last served each frame's navigation: ``(seq, state)``.
#: ``state`` is ``"real"`` for a real document, ``"stub"`` for its
#: client-redirect document not yet committed, and ``"stub_committed"`` once
#: it has; the frame's next commit replaces it (:func:`note_frame_navigated`).
#: Per frame, so heavy navigation elsewhere cannot evict a popup's entry;
#: ``seq`` orders it against entries that were parked while their request had
#: no frame (``_SERVED_UNFRAMED``). Read by :func:`served_client_redirect_last`.
_SERVED_LAST: weakref.WeakKeyDictionary[Any, tuple[int, str]] = weakref.WeakKeyDictionary()

#: The same for a navigation whose request had no frame when it was served --
#: a popup's first request (see ``_UNFRAMED``) -- until the frame appears.
#: Bounded like ``_UNFRAMED``.
_SERVED_UNFRAMED: weakref.WeakKeyDictionary[Any, tuple[int, str]] = weakref.WeakKeyDictionary()
_MAX_SERVED_UNFRAMED = 64

_served_seq = itertools.count(1)

#: What the frame's next commit makes of a record (:func:`note_frame_navigated`).
_AFTER_COMMIT = {"stub": "stub_committed", "stub_committed": "real"}


def _record_served(frame: Any, entry: tuple[int, str]) -> None:
    """Record *entry* for *frame* unless a later one is already there."""
    current = _SERVED_LAST.get(frame)
    if current is None or current[0] < entry[0]:
        _SERVED_LAST[frame] = entry


def _adopt_served_unframed() -> None:
    """Hand every parked served entry whose request now has a frame to that frame."""
    for request in list(_SERVED_UNFRAMED):
        try:
            frame = request.frame
        except Exception as exc:  # still no page for it: keep it parked for the next look
            log.debug("octowright.ssrf.served_document_still_unframed", error=repr(exc))
            continue
        entry = _SERVED_UNFRAMED.pop(request, None)
        if entry is not None:
            _record_served(frame, entry)


def _note_served(request: Any, *, client_redirect: bool) -> None:
    entry = (next(_served_seq), "stub" if client_redirect else "real")
    try:
        frame = request.frame
    except Exception:  # a popup's first request: no frame until its page exists
        frame = None
    try:
        if frame is not None:
            _record_served(frame, entry)
            return
        while len(_SERVED_UNFRAMED) >= _MAX_SERVED_UNFRAMED:
            _SERVED_UNFRAMED.pop(next(iter(_SERVED_UNFRAMED)), None)
        _SERVED_UNFRAMED[request] = entry
    except TypeError:  # a frame or request double that cannot be weakly referenced
        log.debug("octowright.ssrf.served_document_untracked")


def note_frame_navigated(frame: Any) -> None:
    """A document committed in *frame*: the stub's own commit, or the document that replaced it.

    Counted, not compared by URL: chromium reports a popup's committed stub as
    ``chrome-error://chromewebdata/`` (measured, Playwright 1.62), so the URL
    does not say which document committed. The commit after the stub's own
    replaces it, whoever served it -- the guard, a service worker the route
    never sees, or the browser's error page for a refused hop. Registered for
    every page of a guarded context (:func:`install_navigation_guard`).
    """
    if _SERVED_UNFRAMED:
        _adopt_served_unframed()
    try:
        entry = _SERVED_LAST.get(frame)
        if entry is not None and entry[1] in _AFTER_COMMIT:
            _SERVED_LAST[frame] = (entry[0], _AFTER_COMMIT[entry[1]])
    except TypeError:  # a frame double that cannot be weakly referenced
        log.debug("octowright.ssrf.frame_navigation_untracked")


def _watch_page(page: Any) -> None:
    try:
        page.on("framenavigated", note_frame_navigated)
    except Exception as exc:
        log.debug("octowright.ssrf.frame_navigation_unwatched", error=repr(exc))


def served_client_redirect_last(frame: Any) -> bool:
    """Whether *frame* is showing the guard's client-redirect document, as far as the guard knows.

    What a caller that only saw load states cannot tell after the fact: a
    ``domcontentloaded`` it awaited may have been the redirect document's, and
    a page that has since closed can no longer be asked. Readable after the
    page closed (measured on all three engines). True once the guard served
    the frame a client-redirect document, until it serves the frame a real
    one or the frame commits another document after the stub's own
    (:func:`note_frame_navigated`).
    """
    if _SERVED_UNFRAMED:
        _adopt_served_unframed()
    try:
        entry = _SERVED_LAST.get(frame)
    except TypeError:  # a frame double that cannot be weakly referenced
        return False
    return entry is not None and entry[1] != "real"


class FrameChain:
    """Whether the guard ended a navigation in one frame, for a caller that cannot see it.

    A caller waiting on the browser alone cannot always tell. On chromium a
    ``goto`` whose LATER hop was refused -- the client-redirect document's own
    navigation, not the ``goto``'s -- resolves on the error page instead of
    raising; on firefox and webkit that hop leaves the client-redirect document
    in place and fires no event at all, so a ``goto`` or a popup waiting for a
    load state waits out its whole timeout (all measured).

    ``refused`` is set for either ending; ``failed`` says which it was. A hop
    whose fetch failed (DNS, reset, TLS) is a network error, not the policy
    refusing the caller's input, and :meth:`error` keeps the two apart.

    ``hops`` counts the consecutive guard-issued redirects of the chain in
    progress. Each hop of a chain is its own navigation here, so the browser's
    own redirect limit never applies and ``/loop -> /loop`` would spin forever.
    It lives on the record rather than on the frame so that a tool navigation,
    which begins a fresh record, starts from zero: counted per frame, an older
    chain 18 hops in left the tool's own URL two redirects to live. It is reset
    whenever a chain in this record ends -- a real (non-redirect) response is
    served, or a hop is refused or fails -- since the frame's next navigation
    the page starts itself continues in the same record, and an abort that left
    its count behind would start that unrelated chain partway to the limit.
    """

    __slots__ = ("failed", "hops", "reason", "refused")

    def __init__(self) -> None:
        self.refused = asyncio.Event()
        self.reason: str | None = None
        self.failed = False
        self.hops = 0

    def error(self) -> Exception:
        """The exception a caller raises for this chain's ending."""
        if self.failed:
            return NavigationFailedError(self.reason or "navigation failed")
        return InvalidRequestError(self.reason or "navigation refused by the SSRF policy")

    def end(self, reason: str, *, failed: bool) -> None:
        self.reason = reason
        self.failed = failed
        self.refused.set()


_FRAME_CHAINS: weakref.WeakKeyDictionary[Any, FrameChain] = weakref.WeakKeyDictionary()


#: Endings of navigations that had no frame yet, keyed by their request, until
#: the frame appears. A popup's FIRST request is one (measured on all three
#: engines): Playwright raises on ``request.frame`` until the popup page
#: exists, and the popup page arrives after the route handler has already run.
#: The same request resolves its frame once the page does, so the ending is
#: handed to that frame's chain on the next look. Bounded: a request whose
#: frame never appears (nothing else raises there today) must not accumulate.
_UNFRAMED: weakref.WeakKeyDictionary[Any, tuple[str, bool]] = weakref.WeakKeyDictionary()
_MAX_UNFRAMED = 64


def _park_unframed(request: Any, reason: str, *, failed: bool) -> None:
    while len(_UNFRAMED) >= _MAX_UNFRAMED:
        _UNFRAMED.pop(next(iter(_UNFRAMED)), None)
    try:
        _UNFRAMED[request] = (reason, failed)
    except TypeError:  # a request double that cannot be weakly referenced
        log.debug("octowright.ssrf.unframed_ending_dropped", reason=reason)


def _adopt_unframed() -> None:
    """Hand every parked ending whose request now has a frame to that frame's chain."""
    for request in list(_UNFRAMED):
        try:
            frame = request.frame
        except Exception:  # still no page for it: keep it parked for the next look
            continue
        reason, failed = _UNFRAMED.pop(request)
        _chain_for(frame).end(reason, failed=failed)


def _chain_for(frame: Any) -> FrameChain:
    chain = _FRAME_CHAINS.get(frame)
    if chain is None:
        chain = _FRAME_CHAINS[frame] = FrameChain()
    return chain


def frame_chain(frame: Any) -> FrameChain:
    """The guard's record for *frame*, created empty on first use."""
    if _UNFRAMED:
        _adopt_unframed()
    return _chain_for(frame)


def begin_navigation(frame: Any) -> FrameChain | None:
    """A fresh record for a navigation about to start in *frame*; ``None`` with the policy off.

    Only :func:`guarded_navigation` calls this, and it is what reads the
    record: on chromium a ``goto`` whose LATER hop the guard refused resolves
    on the error page instead of raising (measured), so the wait alone would
    report success. An ending of a navigation the guard was already handling
    in *frame* went to the record this one replaces (see :func:`_end_chain`),
    so it cannot end this one, and that navigation's redirects are not counted
    against this one's bound (:attr:`FrameChain.hops`).
    """
    if not ssrf.policy_enabled():
        return None
    if _UNFRAMED:
        # An earlier navigation's parked ending belongs to that one, not this.
        _adopt_unframed()
    chain = _FRAME_CHAINS[frame] = FrameChain()
    return chain


async def guarded_navigation(frame: Any, navigation: Awaitable[T]) -> T:
    """Await a tool's own navigation of *frame* (a ``goto``/``go_back`` not yet awaited), with the guard's verdict.

    Every tool navigation goes through here, so none can forget half of it.
    With the policy off it is just ``await navigation``. With it on, the wait
    also ends as soon as the guard refuses or fails a hop in *frame* -- on
    firefox and webkit a refused LATER hop fires no event, so the ``goto``
    would otherwise wait out its whole timeout -- and it raises that ending
    (:meth:`FrameChain.error`) instead of the browser's own report of it
    (``net::ERR_BLOCKED_BY_CLIENT``, which names neither the hop nor the
    reason) or, on chromium, instead of resolving on the error page. Takes the
    awaitable rather than the page so the Playwright call stays in the gated
    caller.
    """
    chain = begin_navigation(frame)
    if chain is None:
        return await navigation
    try:
        ended, result = await _first_of(navigation, chain)
    except Exception as exc:
        if chain.refused.is_set():
            raise chain.error() from exc
        raise
    if ended:
        raise chain.error()
    return cast("T", result)  # not ended: result is the navigation's own


async def until_refused(wait: Awaitable[Any], chain: FrameChain) -> str | None:
    """Await *wait* unless the guard ends a navigation in *chain*'s frame first.

    Returns the ending's reason, or ``None`` once *wait* finished. An ending
    recorded before the call wins at once -- the frame is fresh, so it is this
    one's.
    """
    ended, _result = await _first_of(wait, chain)
    return chain.reason if ended else None


async def _first_of(wait: Awaitable[T], chain: FrameChain) -> tuple[bool, T | None]:
    """``(True, None)`` if *chain* ended before *wait* finished, else ``(False, wait's result)``."""
    if chain.refused.is_set():
        _discard(wait)
        return True, None
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
        return True, None
    return False, waiting.result()


def _discard(wait: Awaitable[Any]) -> None:
    """Close a coroutine that will not be awaited, so it is not reported as never awaited."""
    close = getattr(wait, "close", None)
    if close is not None:
        close()


def _request_frame(request: Any) -> Any:
    """*request*'s frame, or ``None`` (logged) when it has none."""
    try:
        return request.frame
    except Exception as exc:
        # A service-worker request has no frame, and nor does a popup's first
        # navigation (measured: Playwright raises until the popup page exists).
        # Neither is bounded by the hop limit; an ending of one is parked until
        # its frame appears (``_UNFRAMED``).
        log.debug("octowright.ssrf.request_without_frame", error=repr(exc))
        return None


def _step(chain: FrameChain | None, target: str) -> None:
    """Count one guard-issued redirect, to *target*, against *chain*'s bound."""
    if chain is None:
        return
    chain.hops += 1
    if chain.hops > MAX_REDIRECT_HOPS:
        raise RedirectBlocked(f"redirect chain reaching {target!r} exceeded {MAX_REDIRECT_HOPS} hops")


def _end_chain(request: Any, chain: FrameChain | None, refusal: str | None = None, *, failed: bool = False) -> None:
    """End *request*'s chain; *refusal* says the navigation was refused, or *failed*.

    *chain* is the frame's record as it was when the guard started on
    *request* (:func:`chain_at_start`), and its hop count is reset whichever
    record is current. If a tool navigation has begun a fresh one since, the
    ending belongs to the navigation it replaced and is dropped: recorded, it
    would end the tool's chain -- and cancel its ``goto`` -- for a URL the tool
    never asked for.
    """
    if chain is not None:
        chain.hops = 0
    frame = _request_frame(request)
    if frame is None:
        if refusal is not None:
            _park_unframed(request, refusal, failed=failed)
        return
    if chain is not None and _FRAME_CHAINS.get(frame) is not chain:
        log.debug("octowright.ssrf.stale_navigation_ending_dropped", url=request.url, reason=refusal)
        return
    if refusal is not None:
        frame_chain(frame).end(refusal, failed=failed)


def chain_at_start(request: Any) -> FrameChain | None:
    """The record a navigation *request* belongs to, taken when the guard starts on it; ``None`` without a frame."""
    frame = _request_frame(request)
    return None if frame is None else frame_chain(frame)


async def _serve_navigation(route: Any, request: Any, chain: FrameChain | None) -> None:
    """Fetch a navigation once and hand the page only what was validated."""
    try:
        response = await route.fetch(max_redirects=0)
    except Exception as exc:
        # The browser would have shown a network error; say so rather than
        # leaving the intercepted request unanswered.
        log.debug("octowright.ssrf.navigation_fetch_failed", url=request.url, error=repr(exc))
        _end_chain(request, chain, refusal=f"navigation to {request.url!r} failed: {exc}", failed=True)
        await route.abort("failed")
        return
    location = response.headers.get("location")
    if response.status not in _FOLLOWED_REDIRECTS or not location:
        _end_chain(request, chain)
        _note_served(request, client_redirect=False)
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
    _step(chain, target)
    _CLIENT_REDIRECTS[request] = {"status": response.status, "status_text": response.status_text, "location": target}
    _note_served(request, client_redirect=True)
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


async def _handle_route(route: Any, request: Any) -> None:
    """Abort a request the policy refuses; serve a navigation from its own validated fetch."""
    try:
        if not request.is_navigation_request():
            await _handle_subresource(route, request)
            return
        # Taken before any await: a tool navigation that begins while this one
        # is still being checked or fetched gets its own record.
        chain = chain_at_start(request)
        try:
            await _check_hop(request.url)
            await _serve_navigation(route, request, chain)
        except RedirectBlocked as exc:
            log.warning("octowright.ssrf.redirect_blocked", url=request.url, method=request.method, error=str(exc))
            _end_chain(request, chain, refusal=str(exc))
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
    await bounded(
        context.route("**/*", _handle_route),
        operation="browser_install_navigation_guard",
    )
    # Every commit clears a stale stub record (note_frame_navigated),
    # including the popups this context opens later.
    try:
        context.on("page", _watch_page)
        for page in list(getattr(context, "pages", None) or ()):
            _watch_page(page)
    except Exception as exc:
        log.debug("octowright.ssrf.frame_navigation_unwatched", error=repr(exc))
    route_web_socket = getattr(context, "route_web_socket", None)
    if route_web_socket is not None:
        # A glob does not match ws:// URLs (measured); a pattern that matches
        # everything does.
        await bounded(
            route_web_socket(_EVERY_URL, _handle_websocket),
            operation="browser_install_websocket_guard",
        )
    log.debug("octowright.ssrf.navigation_guard_installed")
