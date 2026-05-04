# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for user-initiated navigation recording (real bug 2026-04-25).

When a user types a URL in the address bar or clicks a link, octowright used
to silently miss it — the JSONL action timeline only ever showed the initial
``launch`` event. ``pool._wire_user_navigation_logger`` plus the
``page.on("framenavigated", ...)`` hook fixes that by emitting a
``user_navigation`` action whenever the main frame navigates.

Filters under test:
    - main frame only (subframe / iframe nav must be ignored)
    - about:blank is suppressed (initial state before our launch goto)
    - URLs that match ``session._last_mcp_navigation`` are de-duped (we
      already log "navigate" in BrowserSession.navigate)

NOTE on real-world coverage: like the disconnect tests, these stub the
Playwright event surface. Verifying that real chromium fires
``framenavigated`` for an address-bar nav requires a headed manual run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import pool as pool_module
from octowright.pool import BrowserPool

# ---------------------------------------------------------------------------
# Stubs (mirrors the structure used by tests/test_pool_disconnect.py — kept
# in this file rather than imported across test modules so each test file
# stays self-describing and resilient to the sibling refactoring it.)
# ---------------------------------------------------------------------------


class _FakeRequestEvent:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def fire(self, event: str, *args: Any) -> None:
        for cb in self.handlers.get(event, []):
            cb(*args)


class _FakeFrame:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url


class _FakePage(_FakeRequestEvent):
    def __init__(self) -> None:
        super().__init__()
        self.video = None
        self.url = "about:blank"
        self.goto = AsyncMock(return_value=None)
        self.title = AsyncMock(return_value="Fake")
        self.keyboard = MagicMock()
        self.screenshot = AsyncMock(return_value=None)
        self.close = AsyncMock(return_value=None)
        self._closed = False
        self.main_frame = _FakeFrame("about:blank")

    def is_closed(self) -> bool:
        return self._closed


class _FakeContext(_FakeRequestEvent):
    def __init__(self) -> None:
        super().__init__()
        self.pages: list[_FakePage] = []
        self.tracing = MagicMock()
        self.add_init_script = AsyncMock(return_value=None)
        self.close = AsyncMock(return_value=None)
        self.unroute = AsyncMock(return_value=None)
        self.route = AsyncMock(return_value=None)

    async def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page


class _FakeBrowser(_FakeRequestEvent):
    def __init__(self) -> None:
        super().__init__()
        self.close = AsyncMock(return_value=None)

    async def new_context(self, **_: Any) -> _FakeContext:
        return _FakeContext()


class _FakeBrowserType:
    async def launch(self, **_: Any) -> _FakeBrowser:
        return _FakeBrowser()

    async def launch_persistent_context(self, *_: Any, **__: Any) -> _FakeContext:
        return _FakeContext()


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeBrowserType()
        self.webkit = _FakeBrowserType()
        self.firefox = _FakeBrowserType()

    async def stop(self) -> None:
        return None


class _FakeAsyncPlaywrightCM:
    async def start(self) -> _FakePlaywright:
        return _FakePlaywright()


def _install_playwright_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pool_module, "async_playwright", lambda: _FakeAsyncPlaywrightCM())


def _framenav_handlers(page: Any) -> list[Any]:
    return page.handlers.get("framenavigated", [])


def _load_handlers(page: Any) -> list[Any]:
    return page.handlers.get("load", [])


def _read_recorder(session: Any) -> list[dict[str, Any]]:
    """Read the JSONL recorder log and return the parsed entries."""
    import json

    # Flush by closing the file handle's buffer; recorder.record() already
    # flushes after every line, so just read.
    text = session.log_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.mark.anyio
async def test_load_event_schedules_markdown_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page 'load' event should schedule markdown capture for that page."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="load",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]
    page = session.pages[0]

    # The launch path schedules an initial capture; wait for it to settle so the
    # popup page's explicit load handler can run in isolation.
    for _ in range(10):
        pending = getattr(session, "_pending_markdown_capture", None)
        if pending is None or pending.done():
            break
        await asyncio.sleep(0)

    session.capture_markdown = AsyncMock(return_value=Path("/tmp/fake.md"))
    if (
        getattr(session, "_pending_markdown_capture", None) is not None
        and not getattr(session, "_pending_markdown_capture", None).done()
    ):
        await asyncio.sleep(0)
    handlers = _load_handlers(page)
    assert handlers, "expected page.on('load') handler installed by _wire_listeners"

    for cb in handlers:
        cb()
    await asyncio.sleep(0)

    assert session.capture_markdown.await_count >= 1
    load_calls = [call for call in session.capture_markdown.await_args_list if call.kwargs.get("page") is page]
    assert load_calls, (
        f"expected load handler capture call with page={page!r}; got {session.capture_markdown.await_args_list!r}"
    )
    called_kwargs = load_calls[-1].kwargs
    assert called_kwargs.get("force") is True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_main_frame_navigation_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A framenavigated event for the main frame, with a real URL, must
    produce a ``user_navigation`` JSONL entry."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="nav",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]
    page = session.pages[0]

    handlers = _framenav_handlers(page)
    assert handlers, "expected framenavigated handler installed by _wire_listeners"

    # Synthesise the user typing a URL.
    page.main_frame.url = "https://example.com/about"
    for cb in handlers:
        cb(page.main_frame)

    entries = _read_recorder(session)
    nav_entries = [e for e in entries if e["action"] == "user_navigation"]
    assert len(nav_entries) == 1, entries
    assert nav_entries[0]["url"] == "https://example.com/about"
    assert nav_entries[0]["page_index"] == 0


@pytest.mark.anyio
async def test_subframe_navigation_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Iframe navigations must NOT be recorded — only main-frame nav."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="iframe",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]
    page = session.pages[0]

    other_frame = _FakeFrame("https://ads.example.com/banner")  # NOT the main frame
    for cb in _framenav_handlers(page):
        cb(other_frame)

    entries = _read_recorder(session)
    nav_entries = [e for e in entries if e["action"] == "user_navigation"]
    assert nav_entries == [], f"subframe nav must be ignored; got {nav_entries}"


@pytest.mark.anyio
async def test_about_blank_navigation_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """about:blank navigations are noise (initial page state before goto) —
    skip them."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="blank",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]
    page = session.pages[0]

    page.main_frame.url = "about:blank"
    for cb in _framenav_handlers(page):
        cb(page.main_frame)

    entries = _read_recorder(session)
    nav_entries = [e for e in entries if e["action"] == "user_navigation"]
    assert nav_entries == []


@pytest.mark.anyio
async def test_dedup_against_mcp_navigate(monkeypatch: pytest.MonkeyPatch) -> None:
    """When BrowserSession.navigate(url) is what triggered the navigation,
    the framenavigated handler must skip — we already wrote a 'navigate'
    entry and don't want to double-log."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="dedup",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]
    page = session.pages[0]

    # Simulate the MCP-driven navigate path setting the marker first…
    session._last_mcp_navigation = "https://example.com/page2"
    # …then Playwright firing framenavigated for that same URL.
    page.main_frame.url = "https://example.com/page2"
    for cb in _framenav_handlers(page):
        cb(page.main_frame)

    entries = _read_recorder(session)
    nav_entries = [e for e in entries if e["action"] == "user_navigation"]
    assert nav_entries == [], "expected MCP-initiated nav to be de-duped"


@pytest.mark.anyio
async def test_user_nav_after_mcp_nav_to_different_url_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the user navigates AWAY from the URL we last MCP-navigated to,
    record it as a user_navigation event."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="diverge",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]
    page = session.pages[0]

    session._last_mcp_navigation = "https://example.com/a"
    page.main_frame.url = "https://example.com/b"  # user-typed URL, different
    for cb in _framenav_handlers(page):
        cb(page.main_frame)

    entries = _read_recorder(session)
    nav_entries = [e for e in entries if e["action"] == "user_navigation"]
    assert len(nav_entries) == 1
    assert nav_entries[0]["url"] == "https://example.com/b"


@pytest.mark.anyio
async def test_session_navigate_sets_dedup_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """BrowserSession.navigate(url) must populate _last_mcp_navigation
    BEFORE awaiting page.goto so a framenavigated firing during goto can
    consult it."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="marker",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]

    await session.navigate("https://example.com/marker-test")
    assert session._last_mcp_navigation == "https://example.com/marker-test"


@pytest.mark.anyio
async def test_popup_page_also_gets_framenavigated_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """A popup page registered after launch must ALSO record user_navigation
    events. The handler factory exposed on the session is reused by
    _wire_listeners for each popup."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="popup",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]

    popup = _FakePage()
    session._register_popup(popup)

    handlers = _framenav_handlers(popup)
    assert handlers, "popup page must get framenavigated handler too"

    popup.main_frame.url = "https://popup.example.com/landing"
    for cb in handlers:
        cb(popup.main_frame)

    entries = _read_recorder(session)
    nav_entries = [e for e in entries if e["action"] == "user_navigation"]
    assert len(nav_entries) == 1
    assert nav_entries[0]["url"] == "https://popup.example.com/landing"
    assert nav_entries[0]["page_index"] == 1


@pytest.mark.anyio
async def test_popup_page_also_gets_load_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load listeners used for markdown capture must also be attached to popup pages."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="popup",
        ephemeral=True,
        viewport_w=None,
        viewport_h=None,
    )
    session = pool._sessions[result["instance_id"]]

    popup = _FakePage()
    session._register_popup(popup)

    handlers = _load_handlers(popup)
    assert handlers, "popup page must get load handler too"
