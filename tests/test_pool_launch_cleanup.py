# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.pool import BrowserPool


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

    monkeypatch.setattr("octowright.pool.RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr("octowright.pool.Recorder", fake_recorder)

    with pytest.raises(RuntimeError):
        await pool.launch(kind="chromium", url="https://example.com", headed=False)

    assert context.closed is True
    assert browser.closed is True
    assert recorder_holder["recorder"].closed is True
    assert pool.list_sessions() == []
