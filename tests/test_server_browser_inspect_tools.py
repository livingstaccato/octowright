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
def _patch_pool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MagicMock:
    fake_pool = MagicMock()
    monkeypatch.setattr(_inspect, "pool", fake_pool)
    # Default RECORDINGS_DIR points at the user's real recordings root; pin
    # it under tmp_path so screenshot/capture paths the tools synthesise
    # (log_path.with_suffix(".png")) pass the containment check.
    monkeypatch.setattr(_inspect, "RECORDINGS_DIR", tmp_path)
    return fake_pool


@pytest.fixture
def recordings_dir(tmp_path: Path) -> Path:
    """The patched RECORDINGS_DIR — handy for tests that synthesise log paths."""
    return tmp_path


def _session(log_root: Path | None = None) -> MagicMock:
    s = MagicMock()
    s.protected = False
    # Default log path lives under the patched RECORDINGS_DIR so the
    # containment-checked screenshot/capture default paths pass. Callers can
    # pass an explicit log_root when they need to assert on disk.
    root = log_root if log_root is not None else Path("/tmp")
    s.log_path = root / "rec.jsonl"
    s.page.url = "https://octowright.com"
    s.page.title = AsyncMock(return_value="Example")
    s.page.locator.return_value.aria_snapshot = AsyncMock(return_value="aria-content")
    s.snapshot = AsyncMock(return_value={"aria": "aria-content", "url": "https://octowright.com", "title": "Example"})
    s.screenshot = AsyncMock(return_value=Path("/tmp/shot.png"))
    s.evaluate = AsyncMock(return_value={"k": "v"})
    s.wait_for = AsyncMock(return_value=None)
    s.expect_url = AsyncMock(return_value="https://octowright.com")
    s.expect_text = AsyncMock(return_value="hello")
    s.expect_selector = AsyncMock(return_value=None)
    s.expect_js = AsyncMock(return_value=True)
    s.console = [{"level": "info", "text": "x"}, {"level": "error", "text": "boom"}]
    s.recorder = MagicMock()
    return s


@pytest.mark.anyio
async def test_snapshot_truncates_and_records(_patch_pool: MagicMock) -> None:
    s = _session()
    s.snapshot = AsyncMock(return_value={"aria": "x" * 20, "url": "https://octowright.com", "title": "Example"})
    _patch_pool.get.return_value = s
    out = await _inspect.browser_snapshot("i", max_chars=5)
    assert out["truncated"] is True
    assert out["aria_size"] == 20
    # The MCP tool must route through session.snapshot so the JSONL gets a
    # "snapshot" event — bypassing it would hide MCP-tool snapshots from
    # macro replay / golden diffs / the audit trail.
    s.snapshot.assert_awaited_once_with(selector="body")


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
async def test_screenshot_default_and_evaluate_truncated(_patch_pool: MagicMock, recordings_dir: Path) -> None:
    s = _session(recordings_dir)
    s.screenshot = AsyncMock(return_value=recordings_dir / "rec.png")
    s.evaluate = AsyncMock(return_value="abcdefghij")
    _patch_pool.get.return_value = s
    shot = await _inspect.browser_screenshot("i")
    ev = await _inspect.browser_evaluate("i", "x", max_chars=5)
    assert shot["path"].endswith("rec.png")
    assert ev["truncated"] is True
    assert ev["result_size"] == 10


@pytest.mark.anyio
async def test_wait_recording_capture_export_and_expects(
    monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock, recordings_dir: Path
) -> None:
    s = _session(recordings_dir)
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
    s.snapshot.assert_awaited_once_with(selector="body")


@pytest.mark.anyio
async def test_browser_wait_for_expression_passes_through(_patch_pool: MagicMock) -> None:
    """browser_wait_for(expression=...) forwards the JS predicate to the
    session's wait_for under the new keyword arg."""
    s = _session()
    _patch_pool.get.return_value = s
    expr = "() => document.querySelectorAll('tr').length > 0"
    out = await _inspect.browser_wait_for("i", expression=expr, timeout_ms=500)
    assert out["ok"] is True
    s.wait_for.assert_awaited_once_with(None, None, 500, expression=expr)


@pytest.mark.anyio
async def test_browser_wait_for_selector_still_works(_patch_pool: MagicMock) -> None:
    """Regression: selector-only calls keep their original keyword shape
    and don't inadvertently set expression=."""
    s = _session()
    _patch_pool.get.return_value = s
    await _inspect.browser_wait_for("i", selector="#x", timeout_ms=200)
    s.wait_for.assert_awaited_once_with("#x", None, 200, expression=None)


@pytest.mark.anyio
async def test_browser_wait_for_no_args_networkidle(_patch_pool: MagicMock) -> None:
    """Regression: bare browser_wait_for(instance_id) routes to the
    networkidle branch (selector=None, text=None, expression=None)."""
    s = _session()
    _patch_pool.get.return_value = s
    await _inspect.browser_wait_for("i")
    s.wait_for.assert_awaited_once_with(None, None, None, expression=None)


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
async def test_browser_read_markdown_refreshes_existing_cache(_patch_pool: MagicMock, tmp_path: Path) -> None:
    s = _session()
    _patch_pool.get.return_value = s

    stale_file = tmp_path / "stale.md"
    fresh_file = tmp_path / "fresh.md"
    stale_file.write_text("Old Markdown")
    fresh_file.write_text("Fresh Markdown")

    s.markdown_path = stale_file
    s.capture_markdown = AsyncMock(return_value=fresh_file)

    out = await _inspect.browser_read_markdown("i")

    assert out["markdown"] == "Fresh Markdown"
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
    s.capture_markdown = AsyncMock(return_value=md_file)

    out = await _inspect.browser_read_markdown("i", max_chars=10)

    assert out["truncated"] is True
    assert out["markdown_size"] == 50
    assert len(out["markdown"]) == 10
    assert out["markdown"] == "A" * 10
    s.capture_markdown.assert_awaited_once()


@pytest.mark.anyio
async def test_browser_read_markdown_zero_cap(_patch_pool: MagicMock, tmp_path: Path) -> None:
    s = _session()
    _patch_pool.get.return_value = s

    md_file = tmp_path / "page3.md"
    md_file.write_text("ABCDE")
    s.markdown_path = md_file
    s.capture_markdown = AsyncMock(return_value=md_file)

    out = await _inspect.browser_read_markdown("i", max_chars=0)

    assert out["truncated"] is True
    assert out["markdown"] == ""


def test_top_level_server_exports_browser_read_markdown() -> None:
    from octowright import server

    assert hasattr(server, "browser_read_markdown")


@pytest.mark.anyio
async def test_browser_capture_and_close_with_snapshot(_patch_pool: MagicMock, recordings_dir: Path) -> None:
    s = _session(recordings_dir)
    _patch_pool.get.return_value = s
    _patch_pool.close = AsyncMock()
    out = await _inspect.browser_capture_and_close("i", snapshot=True)
    assert out["closed"] is True
    assert out["aria"] == "aria-content"
    _patch_pool.close.assert_awaited_once_with("i", force=False)


@pytest.mark.anyio
async def test_browser_capture_and_close_refuses_protected_before_side_effects(
    _patch_pool: MagicMock,
    recordings_dir: Path,
) -> None:
    s = _session(recordings_dir)
    s.protected = True
    _patch_pool.get.return_value = s
    _patch_pool.close = AsyncMock()

    out = await _inspect.browser_capture_and_close("i", snapshot=True)

    assert "error" in out
    assert "force=True" in out["error"]
    s.screenshot.assert_not_awaited()
    s.page.locator.assert_not_called()
    _patch_pool.close.assert_not_awaited()


@pytest.mark.anyio
async def test_browser_capture_and_close_force_closes_protected(
    _patch_pool: MagicMock,
    recordings_dir: Path,
) -> None:
    s = _session(recordings_dir)
    s.protected = True
    _patch_pool.get.return_value = s
    _patch_pool.close = AsyncMock(return_value={"closed": True})

    out = await _inspect.browser_capture_and_close("i", snapshot=False, force=True)

    assert out["closed"] is True
    s.screenshot.assert_awaited_once()
    _patch_pool.close.assert_awaited_once_with("i", force=True)


# ─── path-traversal regression for screenshot/capture ────────────────────────


@pytest.mark.anyio
async def test_browser_screenshot_rejects_path_outside_recordings(
    _patch_pool: MagicMock, recordings_dir: Path, tmp_path: Path
) -> None:
    """An explicit MCP-supplied path that escapes RECORDINGS_DIR must raise
    before any disk write happens."""
    s = _session(recordings_dir)
    _patch_pool.get.return_value = s
    outside = tmp_path.parent / "escape" / "evil.png"
    with pytest.raises(ValueError, match="resolves outside"):
        await _inspect.browser_screenshot("i", path=str(outside))
    s.screenshot.assert_not_called()


@pytest.mark.anyio
async def test_browser_capture_and_close_rejects_path_outside_recordings(
    _patch_pool: MagicMock, recordings_dir: Path, tmp_path: Path
) -> None:
    """browser_capture_and_close also confines screenshot_path."""
    s = _session(recordings_dir)
    _patch_pool.get.return_value = s
    _patch_pool.close = AsyncMock()
    outside = tmp_path.parent / "escape" / "evil.png"
    with pytest.raises(ValueError, match="resolves outside"):
        await _inspect.browser_capture_and_close("i", screenshot_path=str(outside))
    s.screenshot.assert_not_called()


@pytest.mark.anyio
async def test_browser_read_markdown_failure(_patch_pool: MagicMock) -> None:
    """When capture_markdown returns None, the tool raises so MCP returns
    a JSON-RPC error rather than a success-shape dict with an ``error``
    field. LLM clients expect raised errors for failures."""
    s = _session()
    _patch_pool.get.return_value = s
    s.markdown_path = None
    s.capture_markdown = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="markdown generation"):
        await _inspect.browser_read_markdown("i")


@pytest.mark.anyio
async def test_browser_snapshot_records_event_in_jsonl(_patch_pool: MagicMock, tmp_path: Path) -> None:
    """Regression: the MCP tool must route through session.snapshot() so the
    JSONL recording gets a "snapshot" event. Bypassing session.snapshot()
    (calling page.locator(...).aria_snapshot() directly) would hide
    MCP-tool snapshots from macro replay, golden diffs, and the audit
    trail.
    """
    import json
    from types import SimpleNamespace
    from typing import Any

    from octowright.recorder import Recorder
    from octowright.session.core import BrowserSession

    log_path = tmp_path / "rec.jsonl"
    recorder = Recorder(log_path)

    page: Any = SimpleNamespace(url="https://example.com")
    page.title = AsyncMock(return_value="Ex")
    page.locator = MagicMock()
    page.locator.return_value.aria_snapshot = AsyncMock(return_value="- main")

    session = BrowserSession(
        instance_id="inst",
        kind="chromium",
        label=None,
        url="https://example.com",
        browser=None,
        context=SimpleNamespace(),
        page=page,
        recorder=recorder,
        log_path=log_path,
    )
    _patch_pool.get.return_value = session

    await _inspect.browser_snapshot("inst", selector="main")

    events = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    snaps = [e for e in events if e.get("action") == "snapshot"]
    assert snaps, f"expected a 'snapshot' event in JSONL, got: {events}"
    assert snaps[0].get("selector") == "main"


@pytest.mark.anyio
async def test_browser_brief(_patch_pool: MagicMock) -> None:
    s = _session()
    _patch_pool.get.return_value = s
    out = await _inspect.browser_brief("i")

    assert out["url"] == "https://octowright.com"
    assert out["title"] == "Example"
    assert "elements" in out


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
