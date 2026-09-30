# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""An ending belongs to the navigation it ended, not to whatever the frame is doing now.

The verdict was recorded per FRAME, so a page's own navigation that was still
in the guard's fetch when a tool navigation began ended the tool's fresh chain
when that fetch failed -- and ``guarded_navigation`` then cancelled a ``goto``
that would have succeeded, naming a URL the tool never asked for.
``test_stale_verdict_live.py`` reproduces it on real engines.
"""

from __future__ import annotations

import asyncio
from typing import Any

from octowright import ssrf_guard
from tests.ssrf_guard.test_chain_walk import _Frame, _handle, _hop, _Request, _Response, _Route
from tests.ssrf_guard.test_chain_walk import policy_on as policy_on  # autouse: block-private, public answers


def _held_route(request: _Request, outcome: _Response | None) -> tuple[_Route, asyncio.Event, asyncio.Event]:
    """A route whose fetch waits for ``release``; ``None`` makes it fail when released."""
    route = _Route({}, request)
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(*_args: Any, **_kwargs: Any) -> _Response:
        entered.set()
        await release.wait()
        if outcome is None:
            raise RuntimeError("connection reset")
        return outcome

    route.fetch = fetch  # type: ignore[method-assign]
    return route, entered, release


async def _older_navigation_ends_after_begin(outcome: _Response | None) -> tuple[ssrf_guard.FrameChain, _Route]:
    frame = _Frame()
    older = _Request("https://public.test/older", frame=frame)
    route, entered, release = _held_route(older, outcome)
    handling = asyncio.ensure_future(_handle(route, older))
    await asyncio.wait_for(entered.wait(), 2)
    chain = ssrf_guard.begin_navigation(frame)
    assert chain is not None
    release.set()
    await handling
    return chain, route


async def test_a_failed_fetch_of_an_older_navigation_does_not_end_a_newer_chain() -> None:
    chain, route = await _older_navigation_ends_after_begin(None)
    assert route.aborted == "failed"  # the older navigation itself is still answered
    assert not chain.refused.is_set()


async def test_a_refusal_of_an_older_navigation_does_not_end_a_newer_chain() -> None:
    chain, route = await _older_navigation_ends_after_begin(_Response(302, "http://169.254.169.254/"))
    assert route.aborted == "blockedbyclient"
    assert not chain.refused.is_set()


async def test_an_older_navigation_does_not_reset_the_newer_chains_hop_count() -> None:
    """A stale ending popped the frame's hop count, loosening the newer chain's loop bound."""
    frame = _Frame()
    older = _Request("https://public.test/older", frame=frame)
    route, entered, release = _held_route(older, _Response(200))
    handling = asyncio.ensure_future(_handle(route, older))
    await asyncio.wait_for(entered.wait(), 2)
    ssrf_guard.begin_navigation(frame)
    for i in range(ssrf_guard.MAX_REDIRECT_HOPS):
        request = _Request(f"https://loop.test/{i}", frame=frame)
        await _handle(_Route({request.url: _Response(302, "https://loop.test/")}, request), request)
    release.set()
    await handling
    request = _Request("https://loop.test/last", frame=frame)
    last = _Route({request.url: _Response(302, "https://loop.test/")}, request)
    await _handle(last, request)
    assert last.aborted == "blockedbyclient", "the stale ending reset the newer chain's hop count"


async def test_guarded_navigation_is_not_cancelled_by_an_older_navigations_failure() -> None:
    frame = _Frame()
    older = _Request("https://public.test/older", frame=frame)
    route, entered, release = _held_route(older, None)
    handling = asyncio.ensure_future(_handle(route, older))
    await asyncio.wait_for(entered.wait(), 2)

    async def goto() -> str:
        release.set()
        await handling  # the older fetch fails while this navigation is in flight
        await asyncio.sleep(0.01)
        return "response"

    assert await asyncio.wait_for(ssrf_guard.guarded_navigation(frame, goto()), 2) == "response"


async def test_a_navigation_begun_after_the_tools_still_ends_its_chain() -> None:
    """The page's own later hops are the tool's chain: a refusal of one still ends it."""
    frame = _Frame()
    chain = ssrf_guard.begin_navigation(frame)
    request = _Request("https://public.test/next", frame=frame)
    await _handle(_Route({request.url: _Response(302, "http://169.254.169.254/")}, request), request)
    assert chain is not None and chain.refused.is_set()


async def test_a_tool_navigation_does_not_inherit_the_old_chains_hop_count() -> None:
    """The count was per FRAME: an old chain 18 hops in left a fresh tool navigation 2 hops to live."""
    frame = _Frame()
    for i in range(ssrf_guard.MAX_REDIRECT_HOPS - 2):
        route = await _hop(frame, f"https://old.test/{i}", _Response(302, f"https://old.test/{i + 1}"))
        assert route.aborted is None
    ssrf_guard.begin_navigation(frame)
    for step in ("https://tool.test/1", "https://tool.test/2", "https://tool.test/3"):
        route = await _hop(frame, step, _Response(302, step + "x"))
        assert route.aborted is None, f"{step} was refused: the tool's chain inherited the old chain's hops"
