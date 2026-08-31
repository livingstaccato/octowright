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

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import octowright.browser_pool.launch_execution as launch_execution_module
import octowright.browser_pool.pool as pool_module
from octowright.browser_pool import BrowserPool
from octowright.recorder import Recorder
from octowright.session import BrowserSession


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
    """Replace the eviction-listener module's `log` (and close_helpers',
    which emits the explicit-close/evicted-externally lines from inside the
    close coordinator) with a shared capture."""
    from octowright.browser_pool import close_helpers as _close_helpers
    from octowright.browser_pool import listeners as _listeners

    cap = _LogCapture()
    monkeypatch.setattr(_listeners, "log", cap)
    monkeypatch.setattr(_close_helpers, "log", cap)
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


def _page_crash_handlers(page: Any) -> list[Any]:
    """Return all callbacks registered via page.on('crash', ...)."""
    return page.handlers.get("crash", [])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_launch_with_unsafe_url_leaves_no_registered_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsafe target URL must be rejected BEFORE the session is registered,
    so a raised launch leaves nothing live in the pool. Previously the URL was
    validated after registration and the cleanup path skipped a registered
    session — a leaked browser the caller never got an instance_id for."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    with pytest.raises(ValueError):
        await pool.launch(
            kind="chromium",
            url="file:///etc/passwd",
            headed=False,
            label="unsafe",
            viewport_w=None,
            viewport_h=None,
        )

    assert pool._sessions == {}


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.parametrize(
    "unsafe_options",
    [
        {"url": "file:///etc/passwd"},
        {"url": "https://octowright.com", "base_url": "file:///etc/passwd"},
    ],
)
async def test_unsafe_launch_allocates_no_session_driver_or_recording(
    monkeypatch: pytest.MonkeyPatch, unsafe_options: dict[str, str]
) -> None:
    """URL rejection is a pure preflight: no temp dir, driver, or log file."""
    calls: list[str] = []
    pool = BrowserPool()

    async def resolve_session_dir(*_args: Any, **_kwargs: Any) -> None:
        calls.append("session_dir")

    async def ensure_pw() -> None:
        calls.append("playwright")

    def log_path(*_args: Any, **_kwargs: Any) -> None:
        calls.append("recording")

    monkeypatch.setattr(pool, "_resolve_session_dir", resolve_session_dir)
    monkeypatch.setattr(pool, "_ensure_pw", ensure_pw)
    monkeypatch.setattr(launch_execution_module, "new_log_path", log_path)

    with pytest.raises(ValueError):
        await pool.launch(kind="chromium", session=True, **unsafe_options)

    assert calls == []


@pytest.mark.live_browser
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_persistent_launch_waits_for_profile_lifecycle_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from octowright.profile_lifecycle import profile_lifecycle_lock

    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()
    allocation_started = asyncio.Event()
    original_resolve = pool._resolve_session_dir

    async def tracked_resolve(*args: Any, **kwargs: Any) -> Any:
        allocation_started.set()
        return await original_resolve(*args, **kwargs)

    monkeypatch.setattr(pool, "_resolve_session_dir", tracked_resolve)

    async with profile_lifecycle_lock("chromium", "cosmo"):
        launch_task = asyncio.create_task(
            pool.launch(kind="chromium", profile="cosmo", url="https://octowright.com", headed=False)
        )
        await asyncio.sleep(0)
        assert not allocation_started.is_set()

    result = await launch_task
    assert allocation_started.is_set()
    await pool.close(result["instance_id"], force=True)


@pytest.mark.live_browser
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_launch_cancelled_during_nav_leaves_no_registered_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling a launch mid-navigation (after registration) must remove and
    close the session — not leave a live browser the caller never received."""
    import asyncio

    _install_playwright_stub(monkeypatch)
    started = asyncio.Event()
    orig_init = _FakePage.__init__

    def _patched_init(self: _FakePage) -> None:
        orig_init(self)

        async def _hang(*_a: Any, **_k: Any) -> None:
            started.set()
            await asyncio.sleep(3600)

        self.goto = _hang  # type: ignore[method-assign,assignment]

    monkeypatch.setattr(_FakePage, "__init__", _patched_init)
    pool = BrowserPool()

    task = asyncio.create_task(
        pool.launch(
            kind="chromium",
            url="https://octowright.com",
            headed=False,
            label="cancel",
            viewport_w=None,
            viewport_h=None,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2.0)
    # Registration precedes goto, so the session is live in the pool right now.
    assert len(pool._sessions) == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert pool._sessions == {}


@pytest.mark.live_browser
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_external_close_evicts_session(monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture) -> None:
    """Synthesize a context 'close' event and verify the session is evicted +
    the eviction log line is emitted. The registry eviction is synchronous
    (asserted immediately); the eviction log line is emitted by the retained
    coordinator task, which needs the loop to run at least once."""
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
    await _wait_until(lambda: any("evicted_externally" in m for m in listeners_log.messages()))


@pytest.mark.live_browser
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
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
    await _wait_until(lambda: any("evicted_externally" in m for m in listeners_log.messages()))


def _capture_session_events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture everything published to the session event bus during a test."""
    from octowright.browser_pool.session_event_bus import session_event_bus

    captured: list[Any] = []
    monkeypatch.setattr(session_event_bus, "publish_nowait", captured.append)
    return captured


@pytest.mark.live_browser
@pytest.mark.anyio
async def test_page_crash_marks_session_and_notifies(
    monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture
) -> None:
    """A renderer crash (page.on('crash')) marks the session and fires a proactive
    crash notification — without evicting (the browser process is still alive)."""
    from octowright.browser_pool.events import SessionCrashedEvent

    _install_playwright_stub(monkeypatch)
    events = _capture_session_events(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium", url="https://octowright.com", headed=False, label="crash", viewport_w=None, viewport_h=None
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    crash_handlers = _page_crash_handlers(session.page)
    assert crash_handlers, "expected a page.on('crash') handler on the initial page"
    for cb in crash_handlers:
        cb()

    # Marked crashed, but NOT evicted — a renderer crash leaves the process alive.
    assert session._crashed is True
    assert iid in pool._sessions
    # A proactive crash notification was published.
    crashed = [e for e in events if isinstance(e, SessionCrashedEvent)]
    assert len(crashed) == 1
    assert crashed[0].instance_id == iid
    assert crashed[0].scope == "renderer"
    assert any("page_crashed" in m for m in listeners_log.messages()), listeners_log.messages()


@pytest.mark.live_browser
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_eviction_after_crash_reports_reason_crashed(
    monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture
) -> None:
    """When a crashed session is then evicted (process death → disconnect), the
    close event and the relaunch message both say 'crashed', not 'user_close'."""
    from octowright.browser_pool.events import SessionClosedEvent

    _install_playwright_stub(monkeypatch)
    events = _capture_session_events(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="crash",
        viewport_w=None,
        viewport_h=None,
        ephemeral=True,
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    for cb in _page_crash_handlers(session.page):
        cb()
    for cb in _disconnect_handlers(session):
        cb()

    assert iid not in pool._sessions
    # The "relaunch" guidance distinguishes a crash from an ordinary close --
    # this is set synchronously by the acceptance seam, no need to wait.
    assert "crashed" in pool._missing_session_message(iid)
    # The SessionClosedEvent is published by the retained coordinator task.
    await _wait_until(lambda: any(isinstance(e, SessionClosedEvent) for e in events))
    closed = [e for e in events if isinstance(e, SessionClosedEvent)]
    assert closed and closed[-1].reason == "crashed"


@pytest.mark.live_browser
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_external_close_without_crash_stays_user_close(
    monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture
) -> None:
    """An external close with no crash marker is honestly reported as user_close."""
    from octowright.browser_pool.events import SessionClosedEvent

    _install_playwright_stub(monkeypatch)
    events = _capture_session_events(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium", url="https://octowright.com", headed=False, label="ext", viewport_w=None, viewport_h=None
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]

    for cb in _close_handlers(session):
        cb()

    # Generic "ended unexpectedly" message, NOT the crash-specific one -- set
    # synchronously by the acceptance seam.
    message = pool._missing_session_message(iid)
    assert "ended unexpectedly" in message
    assert "its process died" not in message
    await _wait_until(lambda: any(isinstance(e, SessionClosedEvent) for e in events))
    closed = [e for e in events if isinstance(e, SessionClosedEvent)]
    assert closed and closed[-1].reason == "user_close"


async def _wait_until(predicate: Any, *, timeout: float = 5.0) -> None:
    """Wait for an async condition without assuming a number of loop turns.

    Full close includes an off-thread manifest transaction.  A fixed number of
    ``sleep(0)`` yields can expire before that worker is scheduled on a loaded
    CI runner even though teardown is progressing normally.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"condition was not met within {timeout:.1f}s")


@pytest.mark.live_browser
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_all_pages_closed_runs_full_session_close(
    monkeypatch: pytest.MonkeyPatch, listeners_log: _LogCapture
) -> None:
    """Last-page close must run the FULL session close, not a bookkeeping-only
    eviction. Otherwise the context (and its profile lock + background tasks)
    survives while the session is gone from the registry — an orphan that
    close_all() can no longer reach."""
    _install_playwright_stub(monkeypatch)
    pool = BrowserPool()

    result = await pool.launch(
        kind="chromium",
        url="https://octowright.com",
        headed=False,
        label="fullclose",
        viewport_w=None,
        viewport_h=None,
    )
    iid = result["instance_id"]
    session = pool._sessions[iid]
    page = session.pages[0]
    assert _page_close_handlers(page), "expected page.on('close') handler"

    page.mark_closed()  # last page gone → fires page.on('close')
    # Wait for the full teardown, not just the registry pop (the coordinator
    # pops _sessions BEFORE awaiting the teardown body, so registry removal
    # races the teardown itself).
    await _wait_until(
        lambda: (
            session.context.close.await_count >= 1
            and any("octowright.browser.evicted_externally" in message for message in listeners_log.messages())
        )
    )

    assert iid not in pool._sessions
    # The context was actually torn down (the whole point) — not just popped.
    assert session.context.close.await_count >= 1
    # Last-page-gone now routes through the SAME external-close acceptance
    # seam as context.close/browser.disconnected (Task 7), so it logs the
    # external line, not the explicit-close one.
    assert any("octowright.browser.evicted_externally" in m for m in listeners_log.messages()), listeners_log.messages()


@pytest.mark.live_browser
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


@pytest.mark.live_browser
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
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
    await _wait_until(lambda: any("evicted_externally" in m for m in listeners_log.messages()))
    evictions = [m for m in listeners_log.messages() if "evicted_externally" in m]
    assert len(evictions) == 1, f"expected exactly one eviction log; got {len(evictions)}"


@pytest.mark.live_browser
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


@pytest.mark.live_browser
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


@pytest.mark.live_browser
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


@pytest.mark.live_browser
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


# ─── failure-containment integration at the tool boundary (Task 13) ────────
#
# Real BrowserSession + BrowserPool wiring (not the Playwright stub above) so
# the operation gate's failure modes are proven through the ACTUAL MCP tool
# functions (server/browser/input.py, server/browser/lifecycle.py) rather
# than the raw gate/pool APIs those already have dedicated coverage for.


def _real_gated_session(instance_id: str, tmp_path: Path, **kwargs: Any) -> BrowserSession:
    context = MagicMock()
    context.close = AsyncMock()
    context.tracing = MagicMock()
    context.on = MagicMock()
    page = MagicMock()
    page.click = AsyncMock()
    log_path = tmp_path / f"{instance_id}.jsonl"
    return BrowserSession(
        instance_id=instance_id,
        kind="chromium",
        label=None,
        url="https://octowright.com",
        browser=None,
        context=context,
        page=page,
        recorder=Recorder(log_path),
        log_path=log_path,
        **kwargs,
    )


async def _wait_for_queue_depth(gate: Any, depth: int) -> None:
    import asyncio

    async with asyncio.timeout(1):
        while gate.snapshot()["queue_depth"] != depth:
            await asyncio.sleep(0)


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_gate_busy_timeout_isolates_failure_at_tool_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holding browser A's gate beyond its (short, test-only) queue timeout
    and calling a real MCP tool on it surfaces ``SessionBusyTimeoutError``
    through the existing tool error path -- no gate-specific response shape,
    no auto-close, no auto-retry -- while browser B, called concurrently,
    succeeds normally and the MCP registry + event loop stay alive."""
    import asyncio

    from octowright.browser_pool import driver_health
    from octowright.server.browser import input as _input
    from octowright.session.operation.gate import SessionBusyTimeoutError
    from tests._pool_invariants import hold_operation, wait_for_active

    pool = BrowserPool()
    session_a = _real_gated_session("A", tmp_path, operation_queue_timeout_seconds=0.08)
    session_b = _real_gated_session("B", tmp_path)
    pool._sessions[session_a.instance_id] = session_a
    pool._sessions[session_b.instance_id] = session_b
    monkeypatch.setattr(_input, "pool", pool)

    release = asyncio.Event()
    holder = asyncio.create_task(hold_operation(session_a, "external_hold", release))
    await wait_for_active(session_a._operation_gate, "external_hold")

    before_restarts = pool.driver_restart_count()

    with pytest.raises(SessionBusyTimeoutError) as excinfo:
        await _input.browser_click(session_a.instance_id, "#buy")
    assert driver_health.is_driver_dead_error(excinfo.value) is False
    assert session_a.recorder.action_count == 0  # the timed-out ticket never entered click's body

    # Browser B, called concurrently, is completely unaffected.
    out_b = await _input.browser_click(session_b.instance_id, "#buy")
    assert out_b == {"ok": True}
    assert session_b.recorder.action_count == 1

    # The MCP tool registry and the event loop stayed alive throughout.
    tool_names = {tool.name for tool in _input.mcp._tool_manager.list_tools()}
    assert "browser_click" in tool_names
    assert asyncio.get_running_loop().is_running()

    # A is still registered -- a gate error never auto-closes a browser.
    assert pool.get(session_a.instance_id) is session_a

    release.set()
    await holder

    # A is fully usable once the holder releases -- no auto-retry needed.
    out_a = await _input.browser_click(session_a.instance_id, "#buy")
    assert out_a == {"ok": True}
    assert session_a.recorder.action_count == 1
    assert pool.driver_restart_count() == before_restarts


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_external_closure_fails_queued_call_without_driver_reset_or_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playwright's own close signal firing on a session with a queued call
    behind it must fail that call cleanly (SessionClosedError) -- it must not
    reach for driver reset or any daemon-restart-adjacent recovery path."""
    import asyncio

    from octowright.browser_pool import close_helpers as _close_helpers
    from octowright.server.browser import input as _input
    from octowright.session.operation.gate import SessionClosedError
    from tests._pool_invariants import hold_operation, wait_for_active, wait_until

    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)
    pool = BrowserPool()
    session_a = _real_gated_session("A", tmp_path)
    pool._sessions[session_a.instance_id] = session_a
    monkeypatch.setattr(_input, "pool", pool)

    release = asyncio.Event()
    holder = asyncio.create_task(hold_operation(session_a, "external_hold", release))
    await wait_for_active(session_a._operation_gate, "external_hold")

    queued = asyncio.create_task(_input.browser_click(session_a.instance_id, "#buy"))
    await _wait_for_queue_depth(session_a._operation_gate, 1)

    before_restarts = pool.driver_restart_count()
    won = pool._accept_external_close_nowait(session_a.instance_id, expected_session=session_a, reason="user_close")
    assert won is not None

    with pytest.raises(SessionClosedError):
        await queued

    assert pool.driver_restart_count() == before_restarts
    assert session_a.instance_id not in pool._sessions

    release.set()
    await holder
    await wait_until(lambda: session_a.instance_id not in pool._closing_sessions)
    session_a.context.close.assert_awaited_once()

    # The rejected click never ran -- teardown legitimately writes its own
    # "close" row, so this checks for the ABSENCE of a "click" row rather
    # than an exact count.
    import json

    recorded_actions = [json.loads(line)["action"] for line in session_a.recorder.log_path.read_text().splitlines()]
    assert "click" not in recorded_actions


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_cancelled_waiter_never_executes_later_and_leaves_no_recorder_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that cancels a still-queued call must never have that call's
    body run later once the gate frees up, and it must leave no JSONL row --
    a cancelled ticket is not a deferred one."""
    import asyncio

    from octowright.server.browser import input as _input
    from tests._pool_invariants import hold_operation, wait_for_active

    pool = BrowserPool()
    session_a = _real_gated_session("A", tmp_path)
    pool._sessions[session_a.instance_id] = session_a
    monkeypatch.setattr(_input, "pool", pool)

    release = asyncio.Event()
    holder = asyncio.create_task(hold_operation(session_a, "external_hold", release))
    await wait_for_active(session_a._operation_gate, "external_hold")

    queued = asyncio.create_task(_input.browser_click(session_a.instance_id, "#buy"))
    await _wait_for_queue_depth(session_a._operation_gate, 1)

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued

    assert session_a._operation_gate.snapshot()["queue_depth"] == 0
    assert session_a.recorder.action_count == 0

    release.set()
    await holder

    # Even after the gate frees up, the cancelled ticket never runs later.
    await asyncio.sleep(0.05)
    assert session_a.recorder.action_count == 0
    session_a.page.click.assert_not_awaited()

    # A fresh call still works normally.
    out = await _input.browser_click(session_a.instance_id, "#buy")
    assert out == {"ok": True}
    assert session_a.recorder.action_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_accepted_close_completes_through_the_mcp_tool_despite_caller_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same cancellation-safety the raw pool.close coordinator has
    (tests/test_browser_pool_branches.py::test_cancelled_close_caller_does_
    not_cancel_accepted_close) must hold through the real ``browser_close``
    MCP tool too: once a close reservation is accepted, cancelling the
    CALLING task must not stop the close from completing."""
    import asyncio

    from octowright.browser_pool import close_helpers as _close_helpers
    from octowright.server.browser import lifecycle as _lifecycle
    from tests._pool_invariants import hold_operation, wait_for_active, wait_for_state, wait_until

    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)
    pool = BrowserPool()
    session_a = _real_gated_session("A", tmp_path)
    pool._sessions[session_a.instance_id] = session_a
    monkeypatch.setattr(_lifecycle, "pool", pool)

    release = asyncio.Event()
    holder = asyncio.create_task(hold_operation(session_a, "long_action", release))
    await wait_for_active(session_a._operation_gate, "long_action")

    close_task = asyncio.create_task(_lifecycle.browser_close(session_a.instance_id, force=True))
    await wait_for_state(session_a._operation_gate, "closing")

    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    release.set()
    await holder
    await wait_until(lambda: session_a.instance_id not in pool._closing_sessions)

    session_a.context.close.assert_awaited_once()
    with pytest.raises(KeyError):
        pool.get(session_a.instance_id)
