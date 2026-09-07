# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tracking pages that existed before octowright could listen for them.

``context.on("page", ...)`` fires only for pages created after it is
registered, and a persistent context can be handed back already holding pages.
Measured against real Chromium (session restore, three seeded tabs): the
relaunched context returned FOUR pages at handback and fired **zero** page
events. Octowright seeds ``session.pages`` with the single page it picked
(``session/core.py``), so the other three were invisible to ``page_list``,
unreachable by ``page_switch``, and unwired for dialogs, downloads, console and
network -- inside a session it otherwise believes it fully owns.

Chromium's restore prompt is suppressed by default now (see
``tests/test_restore_prompt.py``), so in the default deployment this adopts
nothing. It is the backstop for an operator who opts out.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import macros
from octowright.browser_pool.listeners import adopt_untracked_pages
from octowright.recorder import Recorder
from octowright.session import BrowserSession


def _make_page(url: str) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.close = AsyncMock()
    page.goto = AsyncMock()
    return page


def _make_session(tmp_path: Path) -> BrowserSession:
    log_path = tmp_path / "session.jsonl"
    return BrowserSession(
        instance_id="adopt-1",
        kind="chromium",
        label=None,
        url="https://octowright.com",
        browser=MagicMock(),
        context=MagicMock(),
        page=_make_page("https://octowright.com"),
        recorder=Recorder(log_path),
        log_path=log_path,
    )


def _events(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "session.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_pages_present_at_handback_are_adopted(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    restored = [_make_page("https://example.test/a"), _make_page("https://example.test/b")]
    session.context.pages = [session.page, *restored]

    adopted = adopt_untracked_pages(session, session.context)

    assert adopted == 2
    assert session.pages == [session.page, *restored]
    assert session.page_count == 3


def test_the_session_own_page_is_not_adopted_twice(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    session.context.pages = [session.page]

    assert adopt_untracked_pages(session, session.context) == 0
    assert session.pages == [session.page]


def test_adoption_is_idempotent(tmp_path: Path) -> None:
    """It runs after the page event is registered, so it must tolerate overlap."""
    session = _make_session(tmp_path)
    restored = _make_page("https://example.test/a")
    session.context.pages = [session.page, restored]

    assert adopt_untracked_pages(session, session.context) == 1
    assert adopt_untracked_pages(session, session.context) == 0
    assert session.pages.count(restored) == 1


def test_an_adopted_page_is_recorded_as_adopted_not_as_a_popup(tmp_path: Path) -> None:
    """A tab Chromium restored at startup is not a popup, and the row says so."""
    session = _make_session(tmp_path)
    session.context.pages = [session.page, _make_page("https://example.test/a")]

    adopt_untracked_pages(session, session.context)

    kinds = [event["action"] for event in _events(tmp_path)]
    assert "adopted_page" in kinds
    assert "popup_opened" not in kinds


def test_an_adopted_page_gets_the_same_listeners_as_our_own(tmp_path: Path) -> None:
    """Adopting a page but not wiring it would leave it just as dark."""
    session = _make_session(tmp_path)
    restored = _make_page("https://example.test/a")
    session.context.pages = [session.page, restored]

    adopt_untracked_pages(session, session.context)

    wired = {call.args[0] for call in restored.on.call_args_list}
    assert {"console", "dialog", "download", "response", "websocket"} <= wired


def test_an_empty_context_adopts_nothing(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    session.context.pages = []

    assert adopt_untracked_pages(session, session.context) == 0


def test_a_context_that_cannot_be_read_never_raises(tmp_path: Path) -> None:
    """This runs inside launch; a dying context must not fail the launch."""
    session = _make_session(tmp_path)
    context = MagicMock()
    type(context).pages = property(lambda self: (_ for _ in ()).throw(RuntimeError("closed")))

    assert adopt_untracked_pages(session, context) == 0


@pytest.mark.anyio
async def test_adopted_page_is_replay_passive() -> None:
    """An unclassified event kind is counted as a replay ERROR.

    That is the 608-bogus-failures bug in miniature: a recording carrying one
    adopted-page row per restored tab would report each as a failed action.
    """
    session = MagicMock()
    session.diagnostic_bundle = AsyncMock(return_value={})

    executed, errors = await macros._dispatch_simple(session, {"action": "adopted_page"})

    assert (executed, errors) == (0, 0)
