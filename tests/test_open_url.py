# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for session.open_url — the implementation of browser_open_url.

Mock-based: target='tab' goes through ``context.new_page()``; target='window'
goes through ``page.expect_popup`` + ``page.evaluate``. We don't spin up a real
browser here — fidelity tests cover the playwright-level integration.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.recorder import Recorder
from octowright.session import BrowserSession


def _make_page(url: str = "https://octowright.com") -> MagicMock:
    p = MagicMock()
    p.url = url
    p.close = AsyncMock()
    p.goto = AsyncMock()
    p.wait_for_load_state = AsyncMock()
    p.evaluate = AsyncMock()
    return p


def _make_session(tmp_path: Path, url: str = "https://octowright.com") -> BrowserSession:
    log_path = tmp_path / "test.jsonl"
    recorder = Recorder(log_path)
    page = _make_page(url)
    context = MagicMock()
    browser = MagicMock()
    return BrowserSession(
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


def _last_logged(tmp_path: Path) -> dict:
    lines = (tmp_path / "test.jsonl").read_text().splitlines()
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# target='tab'
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_url_tab_appends_page_and_navigates(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    new_page = _make_page("https://target.octowright.com")
    session.context.new_page = AsyncMock(return_value=new_page)

    result = await session.open_url("https://target.octowright.com", target="tab")

    session.context.new_page.assert_awaited_once()
    new_page.goto.assert_awaited_once()
    assert result == {
        "ok": True,
        "target": "tab",
        "page_index": 1,
        "url": "https://target.octowright.com",
    }
    assert session.pages[1] is new_page


@pytest.mark.anyio
async def test_open_url_tab_records_open_event(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    new_page = _make_page("https://target.octowright.com")
    session.context.new_page = AsyncMock(return_value=new_page)

    await session.open_url("https://target.octowright.com", target="tab")

    last = _last_logged(tmp_path)
    assert last["action"] == "open_url"
    assert last["target"] == "tab"
    assert last["page_index"] == 1
    assert last["url"] == "https://target.octowright.com"


@pytest.mark.anyio
async def test_open_url_tab_reports_navigation_failure(tmp_path: Path) -> None:
    """If goto fails, ok=False with the error message — page is still tracked
    so the caller can decide whether to recover or close."""
    session = _make_session(tmp_path)
    new_page = _make_page("about:blank")
    new_page.goto = AsyncMock(side_effect=TimeoutError("nav timeout"))
    session.context.new_page = AsyncMock(return_value=new_page)

    result = await session.open_url("https://slow.octowright.com", target="tab")

    assert result["ok"] is False
    assert "nav timeout" in result["error"]
    assert result["target"] == "tab"
    assert result["page_index"] == 1


class _DebugCapture:
    """provide.telemetry routes through stdlib logging but the dashboard's
    caplog level filter does not always pick up records under the GH-Actions
    runner profile. Patching the module's `log` attribute is the same pattern
    test_pool_disconnect uses for cross-platform parity."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


@pytest.mark.anyio
async def test_open_url_tab_logs_warning_on_nav_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed goto() must be surfaced at warning level (user-action path).
    Without the log, the user sees ok=False but no trail of why — diagnostics
    depend on this."""
    from octowright.session import core_ops_mixin as _ops

    cap = _DebugCapture()
    monkeypatch.setattr(_ops, "log", cap)

    session = _make_session(tmp_path)
    new_page = _make_page("about:blank")
    new_page.goto = AsyncMock(side_effect=TimeoutError("nav timeout"))
    session.context.new_page = AsyncMock(return_value=new_page)

    await session.open_url("https://slow.octowright.com", target="tab")

    events = [name for name, _ in cap.events]
    assert "octowright.open_url.nav_failed" in events


@pytest.mark.anyio
async def test_open_url_window_logs_warning_on_load_state_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window branch's wait_for_load_state failure must also log at warning."""
    from octowright.session import core_ops_mixin as _ops

    cap = _DebugCapture()
    monkeypatch.setattr(_ops, "log", cap)

    session = _make_session(tmp_path)
    popup = _make_page("https://popup.octowright.com")
    popup.wait_for_load_state = AsyncMock(side_effect=TimeoutError("load state timeout"))
    session.page.expect_popup = MagicMock(return_value=_FakePopupCtx(popup))

    await session.open_url("https://popup.octowright.com", target="window")

    events = [name for name, _ in cap.events]
    assert "octowright.open_url.nav_failed" in events
    # window-branch kwargs identify which branch fired.
    matching = [kw for name, kw in cap.events if name == "octowright.open_url.nav_failed"]
    assert any(kw.get("target") == "window" for kw in matching)


# ---------------------------------------------------------------------------
# target='window'
# ---------------------------------------------------------------------------


class _FakePopupCtx:
    """Stands in for playwright's EventContextManagerImpl[Page]."""

    def __init__(self, popup_page: MagicMock) -> None:
        self._popup = popup_page

    async def __aenter__(self) -> _FakePopupCtx:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    @property
    def value(self) -> asyncio.Future[MagicMock]:
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[MagicMock] = loop.create_future()
        fut.set_result(self._popup)
        return fut


@pytest.mark.anyio
async def test_open_url_window_uses_evaluate_and_appends_popup(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.octowright.com")
    session.page.expect_popup = MagicMock(return_value=_FakePopupCtx(popup))

    result = await session.open_url("https://popup.octowright.com", target="window", width=900, height=700)

    session.page.expect_popup.assert_called_once()
    session.page.evaluate.assert_awaited_once()
    # The evaluate call passes our url + size dict.
    args = session.page.evaluate.await_args
    assert args.args[1] == {"u": "https://popup.octowright.com", "w": 900, "h": 700}
    assert result == {
        "ok": True,
        "target": "window",
        "page_index": 1,
        "url": "https://popup.octowright.com",
    }
    assert session.pages[1] is popup


@pytest.mark.anyio
async def test_open_url_window_records_open_event(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    popup = _make_page("https://popup.octowright.com")
    session.page.expect_popup = MagicMock(return_value=_FakePopupCtx(popup))

    await session.open_url("https://popup.octowright.com", target="window")

    last = _last_logged(tmp_path)
    assert last["action"] == "open_url"
    assert last["target"] == "window"
    assert last["page_index"] == 1


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_url_rejects_unknown_target(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    with pytest.raises(ValueError, match="target must be 'tab' or 'window'"):
        await session.open_url("https://x", target="banana")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
