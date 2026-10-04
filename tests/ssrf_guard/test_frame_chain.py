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
    assert frame not in ssrf_guard._FRAME_CHAINS


async def test_a_refused_hop_is_recorded_on_its_frame() -> None:
    frame = _Frame()
    chain = ssrf_guard.begin_navigation(frame)
    request = _Request("https://public.test/", frame=frame)
    await _handle(_Route({"https://public.test/": _Response(302, "http://169.254.169.254/")}, request), request)
    assert chain is not None and chain.refused.is_set()
    assert isinstance(chain.error(), InvalidRequestError)
    assert "169.254.169.254" in str(chain.error())


async def test_a_client_redirect_and_a_served_page_refuse_nothing() -> None:
    frame = _Frame()
    chain = ssrf_guard.begin_navigation(frame)
    for url, response in (("https://a.test/", _Response(302, "/end")), ("https://a.test/end", _Response(200))):
        request = _Request(url, frame=frame)
        await _handle(_Route({url: response}, request), request)
    assert chain is not None and not chain.refused.is_set()


async def test_begin_navigation_forgets_an_earlier_refusal() -> None:
    frame = _Frame()
    ssrf_guard.frame_chain(frame).refused.set()
    chain = ssrf_guard.begin_navigation(frame)
    assert chain is not None and not chain.refused.is_set()


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


class _PopupRequest(_Request):
    """A popup's first request: ``frame`` raises until the popup page exists (measured, all engines)."""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.page_exists = False
        self._popup_frame = _Frame()

    @property  # type: ignore[override]
    def frame(self) -> Any:
        if not self.page_exists:
            raise RuntimeError("Frame for this navigation request is not available")
        return self._popup_frame

    @frame.setter
    def frame(self, _value: Any) -> None:
        pass


async def test_a_refusal_of_a_popups_first_request_reaches_the_popup_once_it_exists() -> None:
    """It was dropped, so ``open_url(target='window')`` returned ok on the error page."""
    request = _PopupRequest("https://public.test/r")
    await _handle(_Route({"https://public.test/r": _Response(302, "http://169.254.169.254/")}, request), request)
    request.page_exists = True
    chain = ssrf_guard.frame_chain(request.frame)
    assert chain.refused.is_set()
    assert isinstance(chain.error(), InvalidRequestError)
    assert "169.254.169.254" in str(chain.error())


async def test_a_parked_ending_is_not_read_as_a_later_navigations() -> None:
    request = _PopupRequest("https://public.test/r")
    await _handle(_Route({"https://public.test/r": _Response(302, "http://169.254.169.254/")}, request), request)
    request.page_exists = True
    # A navigation begun on the popup afterwards starts clean.
    chain = ssrf_guard.begin_navigation(request.frame)
    assert chain is not None and not chain.refused.is_set()


def test_parked_endings_are_bounded() -> None:
    requests = [_PopupRequest(f"https://public.test/{i}") for i in range(ssrf_guard._MAX_UNFRAMED + 10)]
    for request in requests:
        ssrf_guard._park_unframed(request, "refused", failed=False)
    assert len(ssrf_guard._UNFRAMED) <= ssrf_guard._MAX_UNFRAMED
    ssrf_guard._UNFRAMED.clear()


async def test_a_failed_fetch_is_recorded_as_a_failure_not_a_refusal() -> None:
    """A connection reset is a network error; it was raised as the caller's-input type."""
    frame = _Frame()
    chain = ssrf_guard.begin_navigation(frame)
    request = _Request("https://public.test/", frame=frame)
    route = _Route({}, request)

    async def failing(*_args: Any, **_kwargs: Any) -> _Response:
        raise RuntimeError("connection reset")

    route.fetch = failing  # type: ignore[method-assign]
    await _handle(route, request)
    assert chain is not None and chain.refused.is_set() and chain.failed
    error = chain.error()
    assert isinstance(error, ssrf_guard.NavigationFailedError) and "connection reset" in str(error)
    assert not isinstance(error, InvalidRequestError)


async def test_guarded_navigation_with_the_policy_off_just_awaits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY")
    frame = _Frame()

    async def goto() -> str:
        return "response"

    assert await ssrf_guard.guarded_navigation(frame, goto()) == "response"
    assert frame not in ssrf_guard._FRAME_CHAINS


async def test_guarded_navigation_ends_a_wait_the_browser_would_never_end() -> None:
    """firefox/webkit fire nothing after a refused later hop, so the goto ran out its timeout."""
    frame = _Frame()

    async def goto() -> None:
        ssrf_guard.frame_chain(frame).end("hop refused", failed=False)
        await asyncio.Event().wait()

    with pytest.raises(InvalidRequestError, match="hop refused"):
        await asyncio.wait_for(ssrf_guard.guarded_navigation(frame, goto()), 2)


async def test_guarded_navigation_reports_the_verdict_not_the_browsers_error() -> None:
    frame = _Frame()

    async def goto() -> None:
        ssrf_guard.frame_chain(frame).end("fetch of hop failed", failed=True)
        raise RuntimeError("net::ERR_FAILED")

    with pytest.raises(ssrf_guard.NavigationFailedError, match="fetch of hop failed"):
        await ssrf_guard.guarded_navigation(frame, goto())


async def test_guarded_navigation_passes_a_browser_error_the_guard_did_not_cause() -> None:
    frame = _Frame()

    async def goto() -> None:
        raise RuntimeError("Timeout 45000ms exceeded")

    with pytest.raises(RuntimeError, match="Timeout"):
        await ssrf_guard.guarded_navigation(frame, goto())


def test_ssrf_guard_imports_first_in_a_fresh_interpreter() -> None:
    """``import octowright.ssrf_guard`` before ``octowright.session`` was a circular ImportError.

    The guard imports ``session.timeouts``, which loads the session package,
    whose network mixin imported a name back out of the half-initialised guard.
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", "import octowright.ssrf_guard"], capture_output=True, text=True, timeout=120, check=False
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
