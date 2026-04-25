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
new `_wire_close_evictor` hook and the reordered `pool.close()` path.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import pool as pool_module
from octowright.pool import BrowserPool

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


class _FakeBrowser:
    def __init__(self) -> None:
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
    """Replace octowright.pool.async_playwright with a minimal in-memory stub."""

    def _factory() -> _FakeAsyncPlaywrightCM:
        return _FakeAsyncPlaywrightCM()

    monkeypatch.setattr(pool_module, "async_playwright", _factory)


def _close_handlers(session: Any) -> list[Any]:
    """Return all callbacks registered via context.on('close', ...)."""
    return session.context.handlers.get("close", [])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_external_close_evicts_session(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Synthesize a context 'close' event and verify the session is evicted +
    the eviction log line is emitted."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
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

    with caplog.at_level(logging.INFO):
        # Synthesize the OS-close event.
        for cb in handlers:
            cb()

    assert iid not in pool._sessions
    messages = [r.getMessage() for r in caplog.records]
    assert any("evicted_externally" in m for m in messages), messages


@pytest.mark.anyio
async def test_explicit_close_does_not_emit_external_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`pool.close()` must remove the registry entry first; the close-event
    handler should then no-op silently. The 'closed' log line still fires."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
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

    with caplog.at_level(logging.INFO):
        await pool.close(iid)

    assert iid not in pool._sessions
    messages = [r.getMessage() for r in caplog.records]
    assert any("octowright.browser.closed" in m for m in messages), messages
    assert not any("evicted_externally" in m for m in messages), messages


@pytest.mark.anyio
async def test_external_close_after_explicit_close_is_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Replaying the close event after an explicit close must not crash and
    must not double-log."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://example.com",
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

    caplog.clear()
    with caplog.at_level(logging.INFO):
        # Replay the close event a second time — must be a silent no-op.
        for cb in handlers:
            cb()

    messages = [r.getMessage() for r in caplog.records]
    assert not any("evicted_externally" in m for m in messages), messages


@pytest.mark.anyio
async def test_external_close_one_of_two_keeps_survivor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing one session externally must not affect siblings."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    a = await pool.launch(
        kind="chromium",
        url="https://example.com",
        headed=False,
        label="a",
        viewport_w=None,
        viewport_h=None,
    )
    b = await pool.launch(
        kind="chromium",
        url="https://example.com",
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
