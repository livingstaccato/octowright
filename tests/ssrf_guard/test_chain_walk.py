# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit coverage for the redirect-chain walk.

The live test proves the end-to-end block; these pin the loop's own edges,
which are awkward to provoke through a real browser.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from octowright import ssrf
from octowright.ssrf_guard import MAX_REDIRECT_HOPS, RedirectBlocked, _handle_route, _validate_chain


class _Response:
    def __init__(self, status: int, location: str | None = None) -> None:
        self.status = status
        self.headers = {"location": location} if location else {}


class _Request:
    def __init__(self, url: str, method: str = "GET", navigation: bool = True) -> None:
        self.url = url
        self.method = method
        self.headers = {"content-type": "application/x-www-form-urlencoded", "accept": "text/html"}
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


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


def _public_answer(host: str, *_args: Any, **_kwargs: Any) -> list[Any]:
    if host == "rebind.test":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


async def test_terminal_response_ends_the_walk() -> None:
    route = _Route({"https://ok.test/": _Response(200)})
    await _validate_chain(route, "https://ok.test/")
    assert route.fetched == ["https://ok.test/"]


async def test_blocked_hop_is_never_fetched() -> None:
    """The whole point: validation happens before the request goes out."""
    route = _Route(
        {
            "https://public.test/": _Response(302, "http://169.254.169.254/latest/meta-data/"),
            "http://169.254.169.254/latest/meta-data/": _Response(200),
        }
    )
    with pytest.raises(RedirectBlocked):
        await _validate_chain(route, "https://public.test/")
    assert route.fetched == ["https://public.test/"]


async def test_relative_location_is_resolved_before_checking() -> None:
    route = _Route(
        {
            "https://public.test/a": _Response(302, "/b"),
            "https://public.test/b": _Response(200),
        }
    )
    await _validate_chain(route, "https://public.test/a")
    assert route.fetched == ["https://public.test/a", "https://public.test/b"]


async def test_redirect_without_a_location_ends_the_walk() -> None:
    route = _Route({"https://public.test/": _Response(302)})
    await _validate_chain(route, "https://public.test/")
    assert route.fetched == ["https://public.test/"]


async def test_redirect_loop_is_bounded() -> None:
    route = _Route({"https://loop.test/": _Response(302, "https://loop.test/")})
    with pytest.raises(RedirectBlocked, match="exceeded"):
        await _validate_chain(route, "https://loop.test/")
    assert len(route.fetched) == MAX_REDIRECT_HOPS


async def test_non_navigation_request_is_not_chain_checked() -> None:
    route = _Route({})
    await _handle_route(route, _Request("https://x.test/img.png", navigation=False), fulfill_redirects=True)
    assert route.fell_back and route.fetched == []


async def test_post_navigation_is_sent_once_and_fulfilled() -> None:
    """Chain-checking a POST must not double-submit the form."""
    request = _Request("https://x.test/login", method="POST")
    route = _Route({"https://x.test/login": _Response(200)}, request)
    await _handle_route(route, request, fulfill_redirects=True)
    assert route.fetched == ["https://x.test/login"]
    assert route.fulfilled is not None and not route.fell_back


@pytest.mark.parametrize("status", [303, 302, 301])
async def test_post_redirect_to_a_blocked_host_is_aborted(status: int) -> None:
    """POST -> 30x -> metadata: the browser would follow with a GET."""
    request = _Request("https://x.test/login", method="POST")
    route = _Route({"https://x.test/login": _Response(status, "http://169.254.169.254/latest/meta-data/")}, request)
    await _handle_route(route, request, fulfill_redirects=True)
    assert route.aborted == "blockedbyclient"
    assert route.fetched == ["https://x.test/login"]
    assert route.fulfilled is None


async def test_post_redirect_chain_is_walked_with_get_and_later_hops_checked() -> None:
    request = _Request("https://x.test/login", method="POST")
    route = _Route(
        {
            "https://x.test/login": _Response(303, "https://x.test/next"),
            "https://x.test/next": _Response(302, "http://rebind.test/"),
        },
        request,
    )
    await _handle_route(route, request, fulfill_redirects=True)
    assert route.aborted == "blockedbyclient"
    assert route.fetched == ["https://x.test/login", "https://x.test/next"]
    assert route.fetch_methods == ["POST", "GET"]
    # The GET hop carries no body and none of the headers describing one.
    assert route.fetch_overrides[1]["post_data"] == b""
    assert "content-type" not in route.fetch_overrides[1]["headers"]


async def test_clean_post_redirect_is_fulfilled_with_the_3xx() -> None:
    """The browser follows the validated 303 itself; the POST went out once."""
    request = _Request("https://x.test/login", method="POST")
    first = _Response(303, "/done")
    route = _Route({"https://x.test/login": first, "https://x.test/done": _Response(200)}, request)
    await _handle_route(route, request, fulfill_redirects=True)
    assert route.fulfilled is first
    assert route.fetch_methods == ["POST", "GET"]
    assert route.aborted is None


@pytest.mark.parametrize("status", [307, 308])
async def test_method_preserving_post_redirect_is_aborted_even_when_public(status: int) -> None:
    """Later hops would re-send the body and never reach this handler."""
    request = _Request("https://x.test/login", method="POST")
    route = _Route({"https://x.test/login": _Response(status, "https://x.test/elsewhere")}, request)
    await _handle_route(route, request, fulfill_redirects=True)
    assert route.aborted == "blockedbyclient"
    assert route.fetched == ["https://x.test/login"]


async def test_navigation_url_itself_is_checked() -> None:
    """A page-initiated navigation was never pre-flighted by a tool."""
    route = _Route({})
    await _handle_route(route, _Request("http://rebind.test/"), fulfill_redirects=True)
    assert route.aborted == "blockedbyclient"
    assert route.fetched == []


async def test_redirect_to_a_name_resolving_private_is_blocked() -> None:
    route = _Route({"https://public.test/": _Response(302, "http://rebind.test/x")})
    with pytest.raises(RedirectBlocked, match="non-public"):
        await _validate_chain(route, "https://public.test/")
    assert route.fetched == ["https://public.test/"]


async def test_blocked_chain_aborts_the_navigation() -> None:
    route = _Route(
        {"https://public.test/": _Response(302, "http://127.0.0.1:9/x")},
    )
    await _handle_route(route, _Request("https://public.test/"), fulfill_redirects=True)
    assert route.aborted == "blockedbyclient"
    assert not route.fell_back


async def test_clean_chain_hands_the_navigation_back_to_the_browser() -> None:
    """fallback(), not fulfill() -- the browser must own page.url."""
    route = _Route({"https://public.test/": _Response(200)})
    await _handle_route(route, _Request("https://public.test/"), fulfill_redirects=True)
    assert route.fell_back
    assert route.aborted is None


async def test_an_engine_that_cannot_fulfill_a_redirect_gets_a_client_redirect() -> None:
    """``fulfill_redirects=False`` (WebKit): the validated POST redirect is released as a document."""
    request = _Request("https://public.test/submit", method="POST")
    route = _Route(
        {
            "https://public.test/submit": _Response(303, "https://public.test/done"),
            "https://public.test/done": _Response(200),
        },
        request,
    )
    await _handle_route(route, request, fulfill_redirects=False)
    assert route.fulfilled is None and route.aborted is None
    assert route.fulfilled_body is not None and route.fulfilled_body["status"] == 200
    assert "https://public.test/done" in route.fulfilled_body["body"]
