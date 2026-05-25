# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.recorder import Recorder
from octowright.session.core import BrowserSession


@pytest.mark.anyio
async def test_capture_markdown_falls_back_to_html_strip(tmp_path: Path) -> None:
    log_path = tmp_path / "capture-fallback.jsonl"
    recorder = Recorder(log_path)
    page = MagicMock()
    page.url = "https://octowright.com"
    page.content = AsyncMock(return_value="<script>ignore</script><div>hello</div>")
    session = BrowserSession(
        instance_id="m1",
        kind="chromium",
        label="fallback",
        url="https://octowright.com",
        browser=MagicMock(),
        context=MagicMock(),
        page=page,
        recorder=recorder,
        log_path=log_path,
    )

    path = await session.capture_markdown()
    assert path is not None
    assert path.exists()
    assert "<script>" not in path.read_text(encoding="utf-8")
    assert "hello" in path.read_text(encoding="utf-8")
    assert session.markdown_path == path


@pytest.mark.anyio
async def test_capture_markdown_dedups_with_same_url(tmp_path: Path) -> None:
    log_path = tmp_path / "capture-dedupe.jsonl"
    recorder = Recorder(log_path)
    page = MagicMock()
    page.url = "https://octowright.com"
    page.content = AsyncMock(return_value="<div>first</div>")
    session = BrowserSession(
        instance_id="m2",
        kind="chromium",
        label="dedupe",
        url="https://octowright.com",
        browser=MagicMock(),
        context=MagicMock(),
        page=page,
        recorder=recorder,
        log_path=log_path,
    )

    first = await session.capture_markdown()
    assert first is not None
    page.content.reset_mock()
    second = await session.capture_markdown()

    assert second == first
    page.content.assert_not_called()

    page.url = "https://octowright.com/next"
    await session.capture_markdown()
    assert page.content.call_count == 1


@pytest.mark.anyio
async def test_capture_markdown_uses_markitdown_when_available(tmp_path: Path) -> None:
    class _Rendered:
        def __init__(self) -> None:
            self.text = "MARKDOWN_TEXT"

    class _MarkItDown:
        def convert(self, _html: str) -> _Rendered:
            return _Rendered()

    original = sys.modules.get("markitdown")
    sys.modules["markitdown"] = SimpleNamespace(MarkItDown=_MarkItDown)

    try:
        log_path = tmp_path / "capture-markitdown.jsonl"
        recorder = Recorder(log_path)
        page = MagicMock()
        page.url = "https://octowright.com"
        page.content = AsyncMock(return_value="<div>raw</div>")
        session = BrowserSession(
            instance_id="m3",
            kind="chromium",
            label="markitdown",
            url="https://octowright.com",
            browser=MagicMock(),
            context=MagicMock(),
            page=page,
            recorder=recorder,
            log_path=log_path,
        )

        path = await session.capture_markdown()
        assert path is not None
        assert path.read_text(encoding="utf-8") == "MARKDOWN_TEXT"
    finally:
        if original is None:
            del sys.modules["markitdown"]
        else:
            sys.modules["markitdown"] = original


@pytest.mark.anyio
async def test_capture_markdown_falls_back_when_markitdown_fails(tmp_path: Path) -> None:
    class _FailingMarkItDown:
        def convert(self, _html: str) -> str:
            raise RuntimeError("markitdown failed")

    original = sys.modules.get("markitdown")
    sys.modules["markitdown"] = SimpleNamespace(MarkItDown=_FailingMarkItDown)

    try:
        log_path = tmp_path / "capture-fallback-after-error.jsonl"
        recorder = Recorder(log_path)
        page = MagicMock()
        page.url = "https://octowright.com"
        page.content = AsyncMock(return_value="<script>ignore</script><div>hello</div>")
        session = BrowserSession(
            instance_id="m-error",
            kind="chromium",
            label="fallback-error",
            url="https://octowright.com",
            browser=MagicMock(),
            context=MagicMock(),
            page=page,
            recorder=recorder,
            log_path=log_path,
        )

        path = await session.capture_markdown()
        assert path is not None
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "<script>" not in text
        assert "hello" in text
    finally:
        if original is None:
            del sys.modules["markitdown"]
        else:
            sys.modules["markitdown"] = original


@pytest.mark.anyio
async def test_close_records_markdown_path_in_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "close-markdown.jsonl"
    recorder = Recorder(log_path)

    context = MagicMock()
    context.close = AsyncMock(return_value=None)
    context.tracing = MagicMock()
    context.tracing.stop = AsyncMock(return_value=None)

    browser = MagicMock()
    browser.close = AsyncMock(return_value=None)

    page = MagicMock()
    page.url = "https://octowright.com"

    session = BrowserSession(
        instance_id="m4",
        kind="chromium",
        label="close",
        url="https://octowright.com",
        browser=browser,
        context=context,
        page=page,
        recorder=recorder,
        log_path=log_path,
    )
    md_path = log_path.with_suffix(".markdown.md")
    md_path.write_text("# session markdown", encoding="utf-8")
    session.markdown_path = md_path

    await session.close()

    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    closes = [entry for entry in lines if entry.get("action") == "close"]
    assert len(closes) == 1
    assert closes[0]["markdown_path"] == str(md_path)
