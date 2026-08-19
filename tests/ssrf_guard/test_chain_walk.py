# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit coverage for the redirect-chain walk.

The live test proves the end-to-end block; these pin the loop's own edges,
which are awkward to provoke through a real browser.
"""

from __future__ import annotations

import pytest

from octowright.ssrf_guard import MAX_REDIRECT_HOPS, RedirectBlocked, _handle_route, _validate_chain


class _Response:
    def __init__(self, status: int, location: str | None = None) -> None:
        self.status = status
        self.headers = {"location": location} if location else {}


class _Route:
    """Route double that replays a scripted chain and records the walk."""

    def __init__(self, chain: dict[str, _Response]) -> None:
        self.chain = chain
        self.fetched: list[str] = []
        self.aborted: str | None = None
        self.fell_back = False

    async def fetch(self, url: str, max_redirects: int) -> _Response:
        assert max_redirects == 0, "a hop must never follow its own redirect"
        self.fetched.append(url)
        return self.chain[url]

    async def abort(self, reason: str) -> None:
        self.aborted = reason

    async def fallback(self) -> None:
        self.fell_back = True


class _Request:
    def __init__(self, url: str, method: str = "GET", navigation: bool = True) -> None:
        self.url = url
        self.method = method
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


@pytest.fixture(autouse=True)
def policy_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "")


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
    await _handle_route(route, _Request("https://x.test/img.png", navigation=False))
    assert route.fell_back and route.fetched == []


async def test_post_navigation_is_not_replayed() -> None:
    """Chain-checking a POST would double-submit the form."""
    route = _Route({})
    await _handle_route(route, _Request("https://x.test/login", method="POST"))
    assert route.fell_back and route.fetched == []


async def test_blocked_chain_aborts_the_navigation() -> None:
    route = _Route(
        {"https://public.test/": _Response(302, "http://127.0.0.1:9/x")},
    )
    await _handle_route(route, _Request("https://public.test/"))
    assert route.aborted == "blockedbyclient"
    assert not route.fell_back


async def test_clean_chain_hands_the_navigation_back_to_the_browser() -> None:
    """fallback(), not fulfill() -- the browser must own page.url."""
    route = _Route({"https://public.test/": _Response(200)})
    await _handle_route(route, _Request("https://public.test/"))
    assert route.fell_back
    assert route.aborted is None
