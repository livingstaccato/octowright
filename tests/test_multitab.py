# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.recorder import Recorder
from octowright.session import BrowserSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page(url: str = "https://example.com") -> MagicMock:
    """Return a minimal mock that looks like a Playwright Page.

    page.close() must be awaitable so close_page() can await it.
    """
    p = MagicMock()
    p.url = url
    p.close = AsyncMock()
    return p


def _make_session(tmp_path: Path, url: str = "https://example.com") -> BrowserSession:
    """Build a BrowserSession backed by mock objects (no real browser)."""
    log_path = tmp_path / "test.jsonl"
    recorder = Recorder(log_path)

    page = _make_page(url)
    context = MagicMock()
    browser = MagicMock()

    session = BrowserSession(
        instance_id="test-abc",
        kind="chromium",
        label=None,
        url=url,
        browser=browser,
        context=context,
        page=page,
        recorder=recorder,
        log_path=log_path,
    )
    return session


# ---------------------------------------------------------------------------
# _register_popup
# ---------------------------------------------------------------------------


def test_register_popup_appends_to_pages(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    assert len(session.pages) == 1  # initial page at index 0

    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)

    assert len(session.pages) == 2
    assert session.pages[1] is popup


def test_register_popup_records_event(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)

    log = (tmp_path / "test.jsonl").read_text().splitlines()
    # last recorded line should be the popup_opened event
    import json

    last = json.loads(log[-1])
    assert last["action"] == "popup_opened"
    assert last["page_index"] == 1
    assert last["url"] == "https://popup.example.com"


def test_register_popup_attaches_console_listener(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)

    # _register_popup now registers console + dialog + download listeners.
    registered_events = [call[0][0] for call in popup.on.call_args_list]
    assert "console" in registered_events
    assert "dialog" in registered_events
    assert "download" in registered_events


# ---------------------------------------------------------------------------
# list_pages
# ---------------------------------------------------------------------------


def test_list_pages_initial_shape(tmp_path: Path) -> None:
    session = _make_session(tmp_path, url="https://initial.example.com")
    pages = session.list_pages()

    assert len(pages) == 1
    assert pages[0]["index"] == 0
    assert pages[0]["url"] == "https://initial.example.com"
    assert pages[0]["is_active"] is True
    assert "title" in pages[0]


def test_list_pages_is_active_correct_after_popup(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)

    pages = session.list_pages()
    assert len(pages) == 2
    # Initial page (0) is still active.
    assert pages[0]["is_active"] is True
    assert pages[1]["is_active"] is False


# ---------------------------------------------------------------------------
# switch_page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_switch_page_changes_active_page(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)

    result = await session.switch_page(1)

    assert session.page is popup
    assert result["index"] == 1
    assert result["page_count"] == 2


@pytest.mark.anyio
async def test_switch_page_raises_on_out_of_range(tmp_path: Path) -> None:
    session = _make_session(tmp_path)

    with pytest.raises(IndexError):
        await session.switch_page(5)


@pytest.mark.anyio
async def test_switch_page_is_active_reflects_new_selection(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)

    await session.switch_page(1)
    pages = session.list_pages()

    assert pages[0]["is_active"] is False
    assert pages[1]["is_active"] is True


# ---------------------------------------------------------------------------
# close_page
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_page_removes_from_list(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)
    assert len(session.pages) == 2

    result = await session.close_page(1)

    assert len(session.pages) == 1
    assert result["page_count"] == 1
    popup.close.assert_called_once()


@pytest.mark.anyio
async def test_close_page_reassigns_active_when_active_closed(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)

    original_page = session.page
    # Switch to popup, then close it — active should fall back to index 0.
    await session.switch_page(1)
    await session.close_page(1)

    assert session.page is original_page


@pytest.mark.anyio
async def test_close_page_keeps_active_when_inactive_closed(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)

    original_page = session.page
    # Close the popup (index 1) while index 0 is active.
    await session.close_page(1)

    assert session.page is original_page


@pytest.mark.anyio
async def test_close_page_raises_on_last_page(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    assert len(session.pages) == 1

    with pytest.raises(RuntimeError, match="last remaining page"):
        await session.close_page(0)


@pytest.mark.anyio
async def test_close_page_pops_before_awaiting_close(tmp_path: Path) -> None:
    """Regression for C7: ``await target.close()`` fires Playwright's
    ``_on_page_close`` callback synchronously while ``self.pages`` still
    contains the closing page. If a sibling popup auto-closes in the same
    tick (or a concurrent close races), the cascade-eviction check
    ``still_open = [p for p in session.pages if not p.is_closed()]`` would
    evaluate to empty and fire a spurious session eviction — then the
    surviving ``close_page`` code path crashes on ``self.pages.index(self.page)``
    against a cleared session.

    Fix: pop the page from ``self.pages`` BEFORE awaiting ``target.close()``
    so the synchronous callback's view of ``session.pages`` already excludes
    the page we're closing on purpose.
    """
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.example.com")
    session._register_popup(popup)
    assert len(session.pages) == 2

    # Wire a stand-in _on_page_close that records what self.pages looked like
    # the moment the callback fires. The real listener uses this exact
    # snapshot to decide whether to cascade eviction.
    callback_saw_target_in_pages: list[bool] = []

    def _stand_in_on_page_close(*_: object) -> None:
        callback_saw_target_in_pages.append(popup in session.pages)

    session._on_page_close = _stand_in_on_page_close

    # Make popup.close fire the close listener synchronously (mirrors
    # Playwright behaviour). The mock must also be awaitable so close_page
    # can await it.
    original_close = popup.close

    async def _close_and_fire(*args: object, **kwargs: object) -> None:
        # Run the listener inside the await — i.e. at the moment Playwright
        # would synchronously notify subscribers from inside close().
        _stand_in_on_page_close(popup)
        return await original_close(*args, **kwargs)

    popup.close = _close_and_fire  # type: ignore[assignment]
    # popup.is_closed reflects the post-close state once Playwright has
    # closed the page; before the actual close it must still report False.
    closed_flag = {"v": False}
    popup.is_closed = lambda: closed_flag["v"]

    result = await session.close_page(1)

    # The callback fired exactly once and — critically — saw a self.pages
    # that no longer contained the closing popup. If the pop happened AFTER
    # the await as the old code did, this assertion would flip to True.
    assert callback_saw_target_in_pages == [False], (
        f"_on_page_close saw stale self.pages: {callback_saw_target_in_pages}"
    )
    assert popup not in session.pages
    assert result["page_count"] == 1
