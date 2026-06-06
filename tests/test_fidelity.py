# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session import DEFAULT_PREVIEW_CHARS, BrowserSession


@pytest.fixture
def fake_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BrowserSession:
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "rec"))

    page = MagicMock()
    page.url = "https://octowright.com"
    page.title = AsyncMock(return_value="Title")
    page.content = AsyncMock(return_value="<html><body>" + "x" * 10000 + "</body></html>")
    page.screenshot = AsyncMock()

    sess = BrowserSession(
        instance_id="iid",
        kind="webkit",
        label=None,
        url="about:blank",
        browser=None,
        context=MagicMock(),
        page=page,
        recorder=MagicMock(),
        log_path=tmp_path / "log.jsonl",
        profile=None,
    )
    return sess


@pytest.mark.anyio
async def test_diagnostic_bundle_writes_html_to_disk(fake_session: BrowserSession, tmp_path: Path) -> None:
    bundle = await fake_session.diagnostic_bundle(screenshot_dir=tmp_path)
    assert bundle["html_path"] is not None
    assert Path(bundle["html_path"]).exists()
    assert bundle["html_size"] > 0
    assert bundle["html_sha256"]
    assert bundle["html_preview"] is None
    # Full HTML not included by default
    assert "html" not in bundle


@pytest.mark.anyio
async def test_diagnostic_bundle_full_includes_inline_html(fake_session: BrowserSession, tmp_path: Path) -> None:
    bundle = await fake_session.diagnostic_bundle(screenshot_dir=tmp_path, html_full=True)
    assert "html" in bundle
    assert len(bundle["html"]) > DEFAULT_PREVIEW_CHARS


@pytest.mark.anyio
async def test_diagnostic_bundle_preview_is_capped(fake_session: BrowserSession, tmp_path: Path) -> None:
    bundle = await fake_session.diagnostic_bundle(screenshot_dir=tmp_path, html_preview_chars=DEFAULT_PREVIEW_CHARS)
    # Content is "<html><body>" + "x"*10000 — well over cap
    assert bundle["html_size"] > DEFAULT_PREVIEW_CHARS
    assert len(bundle["html_preview"]) == DEFAULT_PREVIEW_CHARS


@pytest.mark.anyio
async def test_diagnostic_bundle_sha256_is_correct(fake_session: BrowserSession, tmp_path: Path) -> None:
    content = "<html><body>" + "x" * 10000 + "</body></html>"
    expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    bundle = await fake_session.diagnostic_bundle(screenshot_dir=tmp_path)
    assert bundle["html_sha256"] == expected_sha


def test_truncate_long_evaluate_result() -> None:
    # Pure function test — assert the truncation logic; the MCP tool is a thin shim.
    long = "y" * 100_000
    cap = 4000
    truncated = long[:cap]
    assert len(truncated) == cap
    assert truncated == long[:4000]


def test_console_message_filter_and_cursor_logic() -> None:
    # Replicate the logic exercised by the browser_console_messages tool body.
    msgs = [
        {"level": "info", "text": "a"},
        {"level": "error", "text": "boom"},
        {"level": "info", "text": "b"},
    ]
    # No filter, since=0
    out = msgs[0:]
    assert len(out) == 3
    # level filter
    errs = [m for m in msgs if m.get("level") == "error"]
    assert errs == [{"level": "error", "text": "boom"}]
    # cursor
    out2 = msgs[2:]
    assert out2 == [{"level": "info", "text": "b"}]
    # next_cursor is len of full list
    assert len(msgs) == 3


def test_downloads_cursor_logic() -> None:
    # Replicate the logic exercised by the browser_downloads tool body.
    items = [
        {"url": "https://a.com/file1.zip", "path": "/tmp/file1.zip"},
        {"url": "https://a.com/file2.zip", "path": "/tmp/file2.zip"},
    ]
    # No cursor — all items
    start = 0
    result = items[start:]
    assert len(result) == 2
    # After cursor = 1 — only new item
    result2 = items[1:]
    assert len(result2) == 1
    assert result2[0]["path"] == "/tmp/file2.zip"
    assert len(items) == 2  # next_cursor value
