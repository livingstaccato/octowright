# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A popup that closes on the guard's client-redirect document is an error in every ordering.

``_settle_guarded_popup`` learned it was on the stub only from its own probe,
so two orderings returned ``ok`` for a popup that never left the stub: closed
before the first probe (the ``domcontentloaded`` it had awaited was the stub's),
and closed after a probe that could not tell. Whether the popup was on the stub
now also comes from what the guard last served its frame
(``ssrf_guard.served_client_redirect_last``), which a closed page still answers
(``test_navigation_verdicts_live.py`` measures that on all three engines).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright import ssrf_guard
from octowright.recorder import Recorder
from octowright.session import BrowserSession


class _Frame:
    pass


class _Request:
    def __init__(self, frame: _Frame) -> None:
        self.frame = frame
        self.url = "https://public.test/r"


class _Popup:
    """A popup whose redirect probe fails, then closes -- or is closed before anything is asked."""

    def __init__(self, *, closed_at_start: bool) -> None:
        self.main_frame = _Frame()
        self.url = "https://public.test/r"
        self._closed = closed_at_start
        self._handlers: dict[str, list[Any]] = {}

    def is_closed(self) -> bool:
        return self._closed

    async def wait_for_load_state(self, *_args: Any, **_kwargs: Any) -> None:
        return None  # the stub's domcontentloaded

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        self._handlers[event].remove(handler)

    async def evaluate(self, *_args: Any, **_kwargs: Any) -> bool:
        # The page is closing under the probe: it cannot tell, and close follows.
        self._closed = True
        loop = asyncio.get_running_loop()
        for handler in list(self._handlers.get("close", [])):
            loop.call_soon(handler)
        raise RuntimeError("Target page, context or browser has been closed")


def _session(tmp_path: Path) -> BrowserSession:
    log_path = tmp_path / "test.jsonl"
    return BrowserSession(
        instance_id="test-abc",
        kind="firefox",
        label=None,
        url="about:blank",
        browser=MagicMock(),
        context=MagicMock(),
        page=MagicMock(),
        recorder=Recorder(log_path),
        log_path=log_path,
    )


@pytest.mark.parametrize("closed_at_start", [True, False], ids=["before-the-probe", "after-an-unsure-probe"])
async def test_a_popup_closed_on_the_stub_is_an_error(tmp_path: Path, closed_at_start: bool) -> None:
    popup = _Popup(closed_at_start=closed_at_start)
    request = _Request(popup.main_frame)
    ssrf_guard._note_served(request, client_redirect=True)
    with pytest.raises(RuntimeError, match="closed before it left the redirect document"):
        await asyncio.wait_for(_session(tmp_path)._settle_guarded_popup(popup), 5)


@pytest.mark.parametrize("closed_at_start", [True, False], ids=["before-the-probe", "after-an-unsure-probe"])
async def test_a_popup_closed_on_its_destination_is_settled(tmp_path: Path, closed_at_start: bool) -> None:
    """As with the policy off: the last document the guard served it was the real one."""
    popup = _Popup(closed_at_start=closed_at_start)
    stub, destination = _Request(popup.main_frame), _Request(popup.main_frame)
    ssrf_guard._note_served(stub, client_redirect=True)
    ssrf_guard._note_served(destination, client_redirect=False)
    assert await asyncio.wait_for(_session(tmp_path)._settle_guarded_popup(popup), 5) is None


def test_the_last_served_document_is_per_frame_and_in_order() -> None:
    mine, other = _Frame(), _Frame()
    requests = [_Request(mine), _Request(other)]
    ssrf_guard._note_served(requests[0], client_redirect=False)
    ssrf_guard._note_served(requests[1], client_redirect=True)
    assert ssrf_guard.served_client_redirect_last(mine) is False
    assert ssrf_guard.served_client_redirect_last(other) is True
    assert ssrf_guard.served_client_redirect_last(_Frame()) is False
