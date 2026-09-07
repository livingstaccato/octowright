# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Registering the same page twice must not list it twice.

``_wire_listeners`` is idempotent per page (a WeakSet), and
``core_ops_mixin.open_url`` guards its own append with ``if new_page not in
self.pages`` -- so the codebase already knew this hazard in one place and not
the other. ``_register_popup``, the handler on the context ``page`` event,
appended unconditionally, so any path that reached it twice for one page (the
event racing an explicit registration, a re-emitted event, a future caller)
put the page in ``session.pages`` twice.

A duplicate is not cosmetic: ``page_index`` is derived from the list position
and ``page_count`` from its length, so the recording, ``page_list`` and
``page_switch`` all disagree with the browser about how many tabs exist.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from octowright.recorder import Recorder
from octowright.session import BrowserSession


def _page(url: str) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.close = AsyncMock()
    return page


def _session(tmp_path: Path) -> BrowserSession:
    log_path = tmp_path / "session.jsonl"
    return BrowserSession(
        instance_id="popup-1",
        kind="chromium",
        label=None,
        url="https://octowright.com",
        browser=MagicMock(),
        context=MagicMock(),
        page=_page("https://octowright.com"),
        recorder=Recorder(log_path),
        log_path=log_path,
    )


def test_registering_the_same_page_twice_lists_it_once(tmp_path: Path) -> None:
    session = _session(tmp_path)
    popup = _page("https://example.test/popup")

    session._register_popup(popup)
    session._register_popup(popup)

    assert session.pages.count(popup) == 1
    assert session.page_count == len(session.pages)


def test_the_sessions_own_page_is_not_appended_again(tmp_path: Path) -> None:
    """The launch page is already in `pages`; the context event can still see it."""
    session = _session(tmp_path)

    session._register_popup(session.page)

    assert session.pages == [session.page]
    assert session.page_count == 1


def test_a_second_registration_records_nothing(tmp_path: Path) -> None:
    """A duplicate popup_opened row would claim a tab opened that did not."""
    session = _session(tmp_path)
    popup = _page("https://example.test/popup")

    session._register_popup(popup)
    before = (tmp_path / "session.jsonl").read_text().count("popup_opened")
    session._register_popup(popup)
    after = (tmp_path / "session.jsonl").read_text().count("popup_opened")

    assert before == after == 1


def test_distinct_pages_are_both_listed(tmp_path: Path) -> None:
    session = _session(tmp_path)
    first, second = _page("https://example.test/a"), _page("https://example.test/b")

    session._register_popup(first)
    session._register_popup(second)

    assert session.pages == [session.page, first, second]
    assert session.page_count == 3
