# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for BrowserPool's external-close eviction (real bug 2026-04-25).

When a user closes a Playwright-managed browser via the OS (cmd-W, red dot,
crash, etc.), the pool used to never notice — the session stayed in
`pool._sessions` forever, `browser_list` over-counted, and any subsequent
`pool.get(id).page.<anything>` raised "Target page, context or browser has
been closed".

These tests stub Playwright with the minimum surface needed to exercise the
new `_wire_close_evictor` hook (which now wires THREE signals: context.close,
browser.disconnected, and per-page page.close) and the reordered
`pool.close()` path.

NOTE on real-world coverage: these tests synthesise the close events directly
through stubs. They CANNOT prove that real Playwright actually fires
``browser.on("disconnected")`` when the user clicks the OS close button on
the last window — that requires manual verification by launching a headed
browser and clicking the red dot. The point of wiring all three signals is
that whichever Playwright actually fires first wins.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import octowright.browser_pool.pool as pool_module
from octowright.browser_pool import BrowserPool


class _LogCapture:
    """Minimal stand-in for caplog that doesn't depend on stdlib-logging
    routing. Patches `log.info` / `log.warning` on the listeners module so
    eviction-log-emission tests are robust across runner platforms (notably
    macOS arm64, where pytest's caplog occasionally fails to receive
    provide.telemetry-routed records under the GH-Actions runner profile).
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []  # (level, event_name, kwargs)

    def info(self, event: str, **kw: Any) -> None:
        self.events.append(("info", event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append(("warning", event, kw))

    def messages(self) -> list[str]:
        return [name for _level, name, _kw in self.events]


@pytest.fixture
def listeners_log(monkeypatch: pytest.MonkeyPatch) -> _LogCapture:
    """Replace the eviction-listener module's `log` (and the lifecycle
    module's, which emits the explicit-close line) with a shared capture."""
    from octowright.browser_pool import lifecycle as _lifecycle
    from octowright.browser_pool import listeners as _listeners

    cap = _LogCapture()
    monkeypatch.setattr(_listeners, "log", cap)
    monkeypatch.setattr(_lifecycle, "log", cap)
    return cap


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeVideo:
    async def path(self) -> str:
        return "/tmp/fake.webm"


class _FakeRequestEvent:
    """Mimic enough of Playwright's evented objects to register/replay handlers."""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def fire(self, event: str, *args: Any) -> None:
        for cb in self.handlers.get(event, []):
            cb(*args)


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
        # Stub frame for framenavigated tests in the dedicated test module.
        self.main_frame = MagicMock()
        self.main_frame.url = "about:blank"

    def is_closed(self) -> bool:
        return self._closed

    def mark_closed(self) -> None:
        """Test helper: flip the is_closed() return value and fire 'close'."""
        self._closed = True
        self.fire("close")


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
    """Real Playwright Browser objects support ``on('disconnected', ...)`` —
    the stub mirrors that so the new evictor signal can be exercised."""

    def __init__(self) -> None:
        super().__init__()
        self.close = AsyncMock(return_value=None)

    async def new_context(self, **_: Any) -> _FakeContext:
        return _FakeContext()


class _FakeBrowserType:
    """Mimics pw.chromium / pw.webkit / pw.firefox."""

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
    """Replace browser_pool.pool.async_playwright with a minimal in-memory stub."""

    def _factory() -> _FakeAsyncPlaywrightCM:
        return _FakeAsyncPlaywrightCM()

    monkeypatch.setattr(pool_module, "async_playwright", _factory)


def _close_handlers(session: Any) -> list[Any]:
    """Return all callbacks registered via context.on('close', ...)."""
    return session.context.handlers.get("close", [])


def _disconnect_handlers(session: Any) -> list[Any]:
    """Return all callbacks registered via browser.on('disconnected', ...)."""
    if session.browser is None:
        return []
    return session.browser.handlers.get("disconnected", [])


def _page_close_handlers(page: Any) -> list[Any]:
    """Return all callbacks registered via page.on('close', ...)."""
    return page.handlers.get("close", [])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_external_close_evicts_session(monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture) -> None:
    """Synthesize a context 'close' event and verify the session is evicted +
    the eviction log line is emitted."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="ext",
        viewport_w=None,
        viewport_h=None,
    )
    iid = result["instance_id"]
    assert iid in pool._sessions

    session = pool._sessions[iid]
    handlers = _close_handlers(session)
    assert handlers, "expected at least one context.on('close') handler"

    # Synthesize the OS-close event.
    for cb in handlers:
        cb()

    assert iid not in pool._sessions
    assert any("evicted_externally" in m for m in listeners_log.messages()), listeners_log.messages()


@pytest.mark.anyio
async def test_browser_disconnected_evicts_session(monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture) -> None:
    """When the underlying browser process dies, Playwright fires
    ``browser.on('disconnected', ...)`` — that signal must also evict."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="disc",
        viewport_w=None,
        viewport_h=None,
        # ephemeral path is the one that has a separate Browser object with
        # the disconnect signal. Persistent contexts don't expose .browser,
        # so the disconnect-handler check below would be vacuously empty.
        ephemeral=True,
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    handlers = _disconnect_handlers(session)
    assert handlers, "expected browser.on('disconnected') handler for ephemeral browser"

    for cb in handlers:
        cb()

    assert iid not in pool._sessions
    assert any("evicted_externally" in m for m in listeners_log.messages()), listeners_log.messages()


@pytest.mark.anyio
async def test_all_pages_closed_evicts_session(monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture) -> None:
    """If every page on the session reports is_closed() True, that's a strong
    signal the user shut everything — evict."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="pages",
        viewport_w=None,
        viewport_h=None,
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    page = session.pages[0]
    handlers = _page_close_handlers(page)
    assert handlers, "expected page.on('close') handler installed by _wire_listeners"

    page.mark_closed()  # flips is_closed() True and fires the close event

    assert iid not in pool._sessions
    assert any("evicted_externally" in m for m in listeners_log.messages()), listeners_log.messages()


@pytest.mark.anyio
async def test_one_page_close_with_survivor_does_not_evict(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a popup closes but the main page stays open, the session must NOT
    be evicted (otherwise dismissing a popup would nuke the whole instance)."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="pop",
        viewport_w=None,
        viewport_h=None,
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    # Simulate a popup being registered.
    popup = _FakePage()
    session._register_popup(popup)
    assert popup in session.pages

    # Close just the popup. The main page is still open.
    popup.mark_closed()

    assert iid in pool._sessions, "main page is still alive, session must survive"


@pytest.mark.anyio
async def test_multiple_signals_only_evict_once(monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture) -> None:
    """Real Playwright might fire context.close AND browser.disconnected for
    the same teardown. The eviction log line should appear at most once."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="dup",
        viewport_w=None,
        viewport_h=None,
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    # Fire all three signals, in arbitrary order.
    for cb in _close_handlers(session):
        cb()
    for cb in _disconnect_handlers(session):
        cb()
    session.pages[0].mark_closed()

    assert iid not in pool._sessions
    evictions = [m for m in listeners_log.messages() if "evicted_externally" in m]
    assert len(evictions) == 1, f"expected exactly one eviction log; got {len(evictions)}"


@pytest.mark.anyio
async def test_explicit_close_does_not_emit_external_log(
    monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture
) -> None:
    """`pool.close()` must remove the registry entry first; the close-event
    handler should then no-op silently. The 'closed' log line still fires."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="explicit",
        viewport_w=None,
        viewport_h=None,
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    # Wire context.close to fire the 'close' event the way real Playwright would,
    # so we can prove the handler is exercised but stays silent.
    handlers_snapshot = list(_close_handlers(session))

    async def _close_and_fire() -> None:
        for cb in handlers_snapshot:
            cb()

    session.context.close = AsyncMock(side_effect=_close_and_fire)

    await pool.close(iid)

    assert iid not in pool._sessions
    messages = listeners_log.messages()
    assert any("octowright.browser.closed" in m for m in messages), messages
    assert not any("evicted_externally" in m for m in messages), messages


@pytest.mark.anyio
async def test_external_close_after_explicit_close_is_noop(
    monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture
) -> None:
    """Replaying the close event after an explicit close must not crash and
    must not double-log."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="twice",
        viewport_w=None,
        viewport_h=None,
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]
    handlers = list(_close_handlers(session))

    await pool.close(iid)
    assert iid not in pool._sessions

    listeners_log.events.clear()
    # Replay the close event a second time — must be a silent no-op.
    for cb in handlers:
        cb()

    messages = listeners_log.messages()
    assert not any("evicted_externally" in m for m in messages), messages


@pytest.mark.anyio
async def test_external_close_one_of_two_keeps_survivor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing one session externally must not affect siblings."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    a = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="a",
        viewport_w=None,
        viewport_h=None,
    )
    b = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="b",
        viewport_w=None,
        viewport_h=None,
    )

    iid_a, iid_b = a["instance_id"], b["instance_id"]
    assert {iid_a, iid_b}.issubset(pool._sessions.keys())

    # Externally close session A only.
    for cb in _close_handlers(pool._sessions[iid_a]):
        cb()

    assert iid_a not in pool._sessions
    assert iid_b in pool._sessions

    listed = {row["instance_id"] for row in pool.list_sessions()}
    assert listed == {iid_b}


@pytest.mark.anyio
async def test_persistent_context_has_no_browser_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """For persistent contexts the Browser handle is None — wire only the
    context.close + page.close signals; do NOT crash trying to reach for
    ``session.browser.on``."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="persist",
        viewport_w=None,
        viewport_h=None,
        profile="some-profile",
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    assert session.browser is None
    # Context close still works.
    for cb in _close_handlers(session):
        cb()
    assert iid not in pool._sessions
