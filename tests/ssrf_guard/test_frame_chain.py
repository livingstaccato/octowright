# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The per-frame record a navigating caller reads the guard's verdict from.

``test_navigation_settles_live.py`` proves the callers end-to-end; these pin
the record itself: what it holds, and that the default policy never makes one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from octowright import ssrf_guard
from octowright.request_errors import InvalidRequestError
from tests.ssrf_guard.test_chain_walk import _Frame, _handle, _Request, _Response, _Route
from tests.ssrf_guard.test_chain_walk import policy_on as policy_on  # autouse: block-private, public answers


def test_with_the_policy_off_no_record_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY")
    frame = _Frame()
    assert ssrf_guard.begin_navigation(frame) is None
    ssrf_guard.raise_if_refused(None)
    assert frame not in ssrf_guard._FRAME_CHAINS


async def test_a_refused_hop_is_recorded_on_its_frame() -> None:
    frame = _Frame()
    chain = ssrf_guard.begin_navigation(frame)
    request = _Request("https://public.test/", frame=frame)
    await _handle(_Route({"https://public.test/": _Response(302, "http://169.254.169.254/")}, request), request)
    assert chain is not None and chain.refused.is_set()
    with pytest.raises(InvalidRequestError, match=r"169\.254\.169\.254"):
        ssrf_guard.raise_if_refused(chain)


async def test_a_client_redirect_and_a_served_page_refuse_nothing() -> None:
    frame = _Frame()
    chain = ssrf_guard.begin_navigation(frame)
    for url, response in (("https://a.test/", _Response(302, "/end")), ("https://a.test/end", _Response(200))):
        request = _Request(url, frame=frame)
        await _handle(_Route({url: response}, request), request)
    assert chain is not None and not chain.refused.is_set()
    ssrf_guard.raise_if_refused(chain)


async def test_begin_navigation_forgets_an_earlier_refusal() -> None:
    frame = _Frame()
    ssrf_guard.frame_chain(frame).refused.set()
    ssrf_guard.raise_if_refused(ssrf_guard.begin_navigation(frame))


async def test_until_refused_returns_when_the_wait_finishes() -> None:
    chain = ssrf_guard.FrameChain()
    assert await ssrf_guard.until_refused(asyncio.sleep(0), chain) is None


async def test_until_refused_ends_a_wait_that_would_never_finish() -> None:
    chain = ssrf_guard.FrameChain()
    never: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    async def refuse() -> None:
        await asyncio.sleep(0.01)
        chain.reason = "refused"
        chain.refused.set()

    refusing = asyncio.ensure_future(refuse())
    assert await asyncio.wait_for(ssrf_guard.until_refused(never, chain), 2) == "refused"
    await refusing
    assert never.cancelled()


async def test_a_refusal_already_recorded_wins_without_waiting() -> None:
    chain = ssrf_guard.FrameChain()
    chain.reason = "earlier"
    chain.refused.set()

    async def never() -> None:
        await asyncio.Event().wait()

    assert await asyncio.wait_for(ssrf_guard.until_refused(never(), chain), 2) == "earlier"


async def test_a_failed_wait_raises() -> None:
    async def boom() -> None:
        raise RuntimeError("load timed out")

    with pytest.raises(RuntimeError, match="load timed out"):
        await ssrf_guard.until_refused(boom(), ssrf_guard.FrameChain())
