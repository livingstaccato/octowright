# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.browser_pool import BrowserPool


class FakeRecorder:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.closed = False

    def record(self, _action: str, **_fields: object) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakePage:
    video = None
    url = "about:blank"
    main_frame = object()

    async def goto(self, _url: str) -> None:
        raise RuntimeError("Executable doesn't exist at /missing/chromium")

    def is_closed(self) -> bool:
        return False

    def on(self, *_args: object) -> None:
        return None


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[Any] = []
        self.closed = False
        self.tracing = SimpleNamespace(start=self._tracing_start)

    async def new_page(self) -> FakePage:
        return FakePage()

    async def close(self) -> None:
        self.closed = True

    async def add_init_script(self, *, script: str) -> None:
        return None

    async def _tracing_start(self, **_kwargs: object) -> None:
        return None

    def on(self, *_args: object) -> None:
        return None


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    async def new_context(self, **_kwargs: object) -> FakeContext:
        return self.context

    async def close(self) -> None:
        self.closed = True

    def on(self, *_args: object) -> None:
        return None


class FakeBrowserType:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    async def launch(self, **_kwargs: object) -> FakeBrowser:
        return self.browser


@pytest.mark.asyncio
async def test_launch_failure_closes_context_browser_and_recorder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = FakeContext()
    browser = FakeBrowser(context)
    pool = BrowserPool()
    pool._pw = SimpleNamespace(chromium=FakeBrowserType(browser))  # type: ignore[assignment]
    recorder_holder: dict[str, FakeRecorder] = {}

    def fake_recorder(path: Path) -> FakeRecorder:
        recorder = FakeRecorder(path)
        recorder_holder["recorder"] = recorder
        return recorder

    monkeypatch.setattr("octowright.browser_pool.pool.RECORDINGS_DIR", tmp_path)
    # Recorder construction lives in launch_pipeline.post_context_setup, as the
    # first statement in its try block (restored there in the Task 10 review
    # follow-up: it must NOT live inside launch_publish._prepare_session_before_
    # publication, or a failure partway through that helper would leave the
    # surrounding except handlers with no live Recorder reference to close
    # deterministically — see test_launch_failure_during_prepublication_setup_
    # closes_recorder_deterministically below).
    monkeypatch.setattr("octowright.browser_pool.launch_pipeline.Recorder", fake_recorder)

    # Navigation failures no longer tear down the browser — the session stays
    # registered and the error is surfaced as nav_warning in the result.
    result = await pool.launch(kind="chromium", url="https://octowright.com", headed=False)
    assert "nav_warning" in result
    assert context.closed is False
    assert browser.closed is False
    assert len(pool.list_sessions()) == 1


@pytest.mark.asyncio
async def test_launch_failure_during_prepublication_setup_closes_recorder_deterministically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failure inside one of ``_prepare_session_before_publication``'s three
    failure-prone awaits (``_expose_viewport_binding`` / ``wire_init_scripts`` /
    ``context.tracing.start``) happens before that helper ever returns, so
    ``post_context_setup``'s ``except`` handler runs with the tuple-unpack
    line never reached. The Recorder must still close deterministically —
    proving ``recorder = Recorder(log_path)`` really is the first statement
    of ``post_context_setup``'s ``try`` block (not something constructed
    inside the helper and only reachable via its return value)."""
    context = FakeContext()
    browser = FakeBrowser(context)
    pool = BrowserPool()
    pool._pw = SimpleNamespace(chromium=FakeBrowserType(browser))  # type: ignore[assignment]
    recorder_holder: dict[str, FakeRecorder] = {}

    def fake_recorder(path: Path) -> FakeRecorder:
        recorder = FakeRecorder(path)
        recorder_holder["recorder"] = recorder
        return recorder

    monkeypatch.setattr("octowright.browser_pool.pool.RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr("octowright.browser_pool.launch_pipeline.Recorder", fake_recorder)

    from octowright.browser_pool import launch_publish as _launch_publish

    async def _boom_wire_init_scripts(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("init script injection failed")

    monkeypatch.setattr(_launch_publish, "wire_init_scripts", _boom_wire_init_scripts)

    with pytest.raises(RuntimeError, match="init script injection failed"):
        await pool.launch(kind="chromium", url="https://octowright.com", headed=False)

    assert recorder_holder["recorder"].closed is True
    assert context.closed is True
    assert browser.closed is True
    assert len(pool.list_sessions()) == 0


@pytest.mark.anyio
async def test_new_tab_redirector_uses_load_state_not_sleep() -> None:
    """Redirector must call wait_for_load_state, not asyncio.sleep."""
    from octowright.browser_pool.launch_pipeline import _make_new_tab_redirector
    from tests._operation_gate_fakes import OperationAwareFake

    waited: list[str] = []
    navigated: list[str] = []

    class FakePage:
        url = "about:blank"

        async def opener(self) -> None:
            return None  # no opener → a user-opened tab, eligible for redirect

        async def wait_for_load_state(self, state: str, *, timeout: float) -> None:
            waited.append(state)

        async def goto(self, url: str) -> None:
            navigated.append(url)

    handler = _make_new_tab_redirector(OperationAwareFake())
    handler(FakePage())
    await asyncio.sleep(0.05)  # let the async task run

    assert "domcontentloaded" in waited
    assert len(navigated) == 1  # redirected to /new-tab


@pytest.mark.anyio
async def test_new_tab_redirector_skips_programmatic_popups() -> None:
    """A popup with an opener (window.open) must NOT be redirected."""
    from octowright.browser_pool.launch_pipeline import _make_new_tab_redirector
    from tests._operation_gate_fakes import OperationAwareFake

    navigated: list[str] = []
    opener_page = object()

    class FakePopup:
        url = "about:blank"

        async def opener(self) -> object:
            return opener_page  # has an opener → programmatic popup

        async def wait_for_load_state(self, state: str, *, timeout: float) -> None:
            pass

        async def goto(self, url: str) -> None:
            navigated.append(url)

    handler = _make_new_tab_redirector(OperationAwareFake())
    handler(FakePopup())
    await asyncio.sleep(0.05)

    assert navigated == []  # left alone


def test_is_blank_newtab_url_matches_engine_new_tabs() -> None:
    """Blank/new-tab detection covers Chromium, Firefox, and WebKit new tabs."""
    from octowright.browser_pool.launch_pipeline import _is_blank_newtab_url

    # Blank + Firefox + WebKit
    assert _is_blank_newtab_url("")
    assert _is_blank_newtab_url(None)
    assert _is_blank_newtab_url("about:blank")
    assert _is_blank_newtab_url("about:newtab")
    assert _is_blank_newtab_url("about:home")
    # Chromium NTP variants (fallback when the extension didn't load)
    assert _is_blank_newtab_url("chrome://newtab/")
    assert _is_blank_newtab_url("chrome://new-tab-page/")
    assert _is_blank_newtab_url("chrome-search://local-ntp/local-ntp.html")
    # Real URLs must NOT be treated as blank
    assert not _is_blank_newtab_url("https://example.com")
    assert not _is_blank_newtab_url("http://127.0.0.1:6286/new-tab")
    # The extension's own page is not "blank" — it self-redirects
    assert not _is_blank_newtab_url("chrome-extension://abc/newtab.html")
