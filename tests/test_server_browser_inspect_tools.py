# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import inspect as _inspect


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_pool = MagicMock()
    monkeypatch.setattr(_inspect, "pool", fake_pool)
    return fake_pool


def _session() -> MagicMock:
    s = MagicMock()
    s.log_path = Path("/tmp/rec.jsonl")
    s.page.url = "https://example.com"
    s.page.title = AsyncMock(return_value="Example")
    s.page.locator.return_value.aria_snapshot = AsyncMock(return_value="aria-content")
    s.screenshot = AsyncMock(return_value=Path("/tmp/shot.png"))
    s.evaluate = AsyncMock(return_value={"k": "v"})
    s.wait_for = AsyncMock(return_value=None)
    s.expect_url = AsyncMock(return_value="https://example.com")
    s.expect_text = AsyncMock(return_value="hello")
    s.expect_selector = AsyncMock(return_value=None)
    s.expect_js = AsyncMock(return_value=True)
    s.console = [{"level": "info", "text": "x"}, {"level": "error", "text": "boom"}]
    s.recorder = MagicMock()
    return s


@pytest.mark.anyio
async def test_snapshot_truncates_and_records(_patch_pool: MagicMock) -> None:
    s = _session()
    s.page.locator.return_value.aria_snapshot = AsyncMock(return_value="x" * 20)
    _patch_pool.get.return_value = s
    out = await _inspect.browser_snapshot("i", max_chars=5)
    assert out["truncated"] is True
    assert out["aria_size"] == 20
    s.recorder.record.assert_called_once()


@pytest.mark.anyio
async def test_snapshot_full_untruncated(_patch_pool: MagicMock) -> None:
    s = _session()
    _patch_pool.get.return_value = s
    out = await _inspect.browser_snapshot("i", full=True)
    assert out["truncated"] is False
    assert out["aria"] == "aria-content"


@pytest.mark.anyio
async def test_evaluate_bytes_and_full(_patch_pool: MagicMock) -> None:
    s = _session()
    s.evaluate = AsyncMock(return_value=b"abc")
    _patch_pool.get.return_value = s
    out = await _inspect.browser_evaluate("i", "1+1", full=True)
    assert out["truncated"] is False
    assert out["result"] == b"abc"


def test_console_messages_filter_and_cursor(_patch_pool: MagicMock) -> None:
    s = _session()
    _patch_pool.get.return_value = s
    out = _inspect.browser_console_messages("i", level="error", since=1)
    assert out["next_cursor"] == 2
    assert len(out["messages"]) == 1


@pytest.mark.anyio
async def test_screenshot_default_and_evaluate_truncated(_patch_pool: MagicMock) -> None:
    s = _session()
    s.screenshot = AsyncMock(return_value=Path("/tmp/rec.png"))
    s.evaluate = AsyncMock(return_value="abcdefghij")
    _patch_pool.get.return_value = s
    shot = await _inspect.browser_screenshot("i")
    ev = await _inspect.browser_evaluate("i", "x", max_chars=5)
    assert shot["path"].endswith("rec.png")
    assert ev["truncated"] is True
    assert ev["result_size"] == 10


@pytest.mark.anyio
async def test_wait_recording_capture_export_and_expects(
    monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock
) -> None:
    s = _session()
    _patch_pool.get.return_value = s
    _patch_pool.close = AsyncMock(return_value={"closed": True})
    monkeypatch.setattr(_inspect, "_export_script", MagicMock(return_value=Path("/tmp/out.py")))
    monkeypatch.setattr(_inspect, "tail_log", MagicMock(return_value=([{"a": 1}], 10, 10)))

    waited = await _inspect.browser_wait_for("i", selector="#a", timeout_ms=1)
    rec_path = _inspect.browser_recording_path("i")
    cap = await _inspect.browser_capture_and_close("i", snapshot=False)
    exported = _inspect.browser_export_script("i", format="python")
    url_ok = await _inspect.browser_expect_url("i", "example")
    text_ok = await _inspect.browser_expect_text("i", "#x", "hello")
    sel_ok = await _inspect.browser_expect_selector("i", "#x", present=False)
    js_ok = await _inspect.browser_expect_js("i", "true")
    tail = _inspect.browser_tail_recording("i")

    assert waited["ok"] is True
    assert rec_path["path"].endswith("rec.jsonl")
    assert cap["closed"] is True
    assert exported["path"] == str(Path("/tmp/out.py"))
    assert url_ok["ok"] and text_ok["ok"] and sel_ok["ok"] and js_ok["ok"]
    assert tail["complete"] is True and tail["cursor"] == 10


@pytest.mark.anyio
async def test_snapshot_default_selector(_patch_pool: MagicMock) -> None:
    s = _session()
    _patch_pool.get.return_value = s
    await _inspect.browser_snapshot("i")
    s.page.locator.assert_called_once_with("body")


@pytest.mark.anyio
async def test_browser_read_markdown(_patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    s = _session()
    _patch_pool.get.return_value = s

    md_file = tmp_path / "page.md"
    md_file.write_text("Hello Markdown")

    s.markdown_path = None
    s.capture_markdown = AsyncMock(return_value=md_file)

    out = await _inspect.browser_read_markdown("i")

    assert out["markdown"] == "Hello Markdown"
    s.capture_markdown.assert_awaited_once()


@pytest.mark.anyio
async def test_browser_read_markdown_truncates(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    s = _session()
    _patch_pool.get.return_value = s

    md_file = tmp_path / "page2.md"
    md_file.write_text("A" * 50)
    s.markdown_path = md_file
    s.capture_markdown = AsyncMock()

    out = await _inspect.browser_read_markdown("i", max_chars=10)

    assert out["truncated"] is True
    assert out["markdown_size"] == 50
    assert out["markdown"].startswith("A" * 10)
    assert out["markdown"].endswith("more chars)")
    s.capture_markdown.assert_not_awaited()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
