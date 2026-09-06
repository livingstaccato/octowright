# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import BrowserPool
from octowright.browser_pool import close_helpers as _close_helpers
from octowright.server.browser import discovery as _discovery
from octowright.server.browser import discovery_links as _discovery_links
from octowright.server.browser import inspect as _inspect
from octowright.server.browser import inspect_assertions as _inspect_assertions
from octowright.server.browser import inspect_capture as _inspect_capture
from octowright.server.browser import inspect_console as _inspect_console
from octowright.server.browser import inspect_recording as _inspect_recording
from octowright.session.operation.gate import SessionClosingError
from tests._aria_stubs import stub_credential_scan
from tests._pool_invariants import wait_for_active, wait_for_state


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MagicMock:
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    fake_pool = MagicMock()
    monkeypatch.setattr(_inspect, "pool", fake_pool)
    monkeypatch.setattr(_discovery, "pool", fake_pool)
    monkeypatch.setattr(_discovery_links, "pool", fake_pool)
    monkeypatch.setattr(_inspect_assertions, "pool", fake_pool)
    monkeypatch.setattr(_inspect_console, "pool", fake_pool)
    monkeypatch.setattr(_inspect_recording, "pool", fake_pool)
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
    stub_credential_scan(s.page.locator.return_value)
    # _target() defaults to the page (no frame switched); brief routes through it.
    s._target.return_value = s.page
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


def _capture_session(log_root: Path | None = None, *, instance_id: str = "i", kind: str = "chromium") -> MagicMock:
    """Like ``_session()`` but with a REAL ``SessionOperationGate``: Task 8's
    capture-and-close runs its capture as a preparation callback INSIDE the
    pool's close coordinator, which drives ``_operation_gate``/
    ``session.operation(...)`` directly -- an unspecced MagicMock's
    auto-mocked (non-awaitable) methods can't stand in for those."""
    from octowright.session.operation.gate import SessionOperationGate

    s = _session(log_root)
    s.instance_id = instance_id
    s.kind = kind
    s.video_path = None
    s.trace_path = None
    s._teardown_after_close_cutoff = AsyncMock()
    gate = SessionOperationGate(instance_id, kind)
    s._operation_gate = gate
    s.operation = gate.operation
    s.operation_snapshot = gate.snapshot
    return s


@pytest.fixture
def capture_pool(monkeypatch: pytest.MonkeyPatch, recordings_dir: Path) -> BrowserPool:
    """A REAL ``BrowserPool`` wired into ``inspect_capture.pool``.

    ``browser_capture_and_close`` (Task 8) runs its capture as a preparation
    callback INSIDE the pool's close coordinator (``_sessions_lock``,
    ``_closing_sessions``, the session's real gate) -- a fully-mocked pool
    can't provide any of that, unlike every OTHER inspect tool in this file.
    """
    pool = BrowserPool()
    monkeypatch.setattr(_inspect_capture, "pool", pool)
    monkeypatch.setattr(_inspect_capture, "RECORDINGS_DIR", recordings_dir)
    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)
    return pool


@pytest.mark.anyio
async def test_snapshot_truncates_and_records(_patch_pool: MagicMock) -> None:
    s = _session()
    s.snapshot = AsyncMock(return_value={"aria": "x" * 20, "url": "https://octowright.com", "title": "Example"})
    _patch_pool.get.return_value = s
    out = await _inspect.browser_snapshot("i", max_chars=5)
    assert out["truncated"] is True
    assert out["aria_size"] == 20
    assert out["actions"] == [
        {"tool": "browser_page_outline", "args": {"instance_id": "i"}},
        {
            "tool": "browser_read_markdown",
            "args": {"instance_id": "i", "response_mode": "summary"},
        },
        {"tool": "browser_snapshot", "args": {"instance_id": "i", "selector": "main"}},
    ]
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
async def test_snapshot_degrades_on_timeout(_patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """A heavy-DOM aria snapshot that would exceed the bridge timeout must degrade
    to a typed result with a hint, not hang until the transport gives up."""
    import asyncio

    s = _session()

    async def _slow(**_: object) -> dict[str, str]:
        await asyncio.sleep(1.0)
        return {"aria": "x", "url": "u", "title": "t"}

    s.snapshot = _slow
    _patch_pool.get.return_value = s
    monkeypatch.setattr(_inspect, "SNAPSHOT_TIMEOUT_S", 0.05, raising=False)

    out = await _inspect.browser_snapshot("i")
    assert out["snapshot_timed_out"] is True
    assert "markdown" in out["hint"].lower()
    assert out["actions"] == [
        {"tool": "browser_page_outline", "args": {"instance_id": "i"}},
        {
            "tool": "browser_read_markdown",
            "args": {"instance_id": "i", "response_mode": "summary"},
        },
        {"tool": "browser_snapshot", "args": {"instance_id": "i", "selector": "main"}},
    ]


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


def test_console_messages_summary_mode_returns_compact_summary(_patch_pool: MagicMock) -> None:
    s = _session()
    s.console = [
        {"level": "info", "text": "boot"},
        {"level": "error", "text": "boom"},
    ]
    _patch_pool.get.return_value = s

    out = _inspect.browser_console_messages("i", level="error", since=0, response_mode="summary")

    assert "messages" not in out
    assert out["count"] == 1
    assert out["error_count"] == 1
    assert out["by_level"] == [{"key": "error", "count": 1}]


def test_console_summary_aggregates_and_bounds_recent_messages(_patch_pool: MagicMock) -> None:
    s = _session()
    s.console = [
        {"level": "info", "text": "boot"},
        {"level": "warning", "text": "slow render"},
        {"level": "error", "text": "first error"},
        {"level": "error", "text": "second error " + ("x" * 500)},
    ]
    _patch_pool.get.return_value = s

    out = _inspect.browser_console_summary("i", recent_limit=2, text_chars=30)

    assert out["total"] == 4
    assert out["next_cursor"] == 4
    assert out["by_level"] == [
        {"key": "error", "count": 2},
        {"key": "info", "count": 1},
        {"key": "warning", "count": 1},
    ]
    assert out["error_count"] == 2
    assert out["recent"] == [
        {
            "index": 2,
            "level": "error",
            "text": "first error",
            "action": {"tool": "browser_console_summary", "args": {"instance_id": "i", "since": 2, "level": "error"}},
        },
        {
            "index": 3,
            "level": "error",
            "text": "second error xxxxxxxxxxxxxxxxx",
            "action": {"tool": "browser_console_summary", "args": {"instance_id": "i", "since": 3, "level": "error"}},
        },
    ]
    assert out["next_actions"] == [
        {"tool": "browser_console_summary", "args": {"instance_id": "i", "since": 4}},
        {"tool": "browser_console_summary", "args": {"instance_id": "i", "level": "error"}},
        {"tool": "capture_create", "args": {"instance_id": "i", "source": "console", "response_mode": "summary"}},
    ]
    assert "messages" not in out


@pytest.mark.anyio
async def test_browser_observe_combines_compact_page_and_diagnostics(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _session()
    _patch_pool.get.return_value = s
    outline = AsyncMock(return_value={"url": "https://octowright.com", "headings": []})
    console_summary = MagicMock(return_value={"error_count": 1, "recent": []})
    network_summary = MagicMock(return_value={"failure_count": 2, "problem_hosts": []})
    downloads_summary = MagicMock(return_value={"count": 0, "recent": []})
    monkeypatch.setattr(_inspect, "browser_page_outline", outline)
    monkeypatch.setattr(_inspect, "browser_console_summary", console_summary)
    monkeypatch.setattr(_inspect, "browser_network_summary", network_summary)
    monkeypatch.setattr(_inspect, "browser_downloads_summary", downloads_summary)

    out = await _inspect.browser_observe("i", include_downloads=True, limit=5)

    assert out["instance_id"] == "i"
    assert out["outline"]["url"] == "https://octowright.com"
    assert out["console"]["error_count"] == 1
    assert out["network"]["failure_count"] == 2
    assert out["downloads"]["count"] == 0
    assert out["actions"] == [
        "browser_page_outline",
        "browser_find_link",
        "browser_find_field",
        "browser_read_markdown",
    ]
    assert out["next_actions"] == [
        {"tool": "browser_page_outline", "args": {"instance_id": "i", "limit": 5}},
        {"tool": "browser_find_link", "args": {"instance_id": "i", "query": "<intent>", "limit": 8}},
        {"tool": "browser_find_field", "args": {"instance_id": "i", "query": "<intent>", "limit": 8}},
        {
            "tool": "browser_read_markdown",
            "args": {"instance_id": "i", "response_mode": "summary"},
        },
        {
            "tool": "capture_create",
            "args": {"instance_id": "i", "source": "snapshot", "response_mode": "summary"},
        },
    ]
    outline.assert_awaited_once_with("i", limit=5)
    console_summary.assert_called_once_with("i")
    network_summary.assert_called_once_with("i")
    downloads_summary.assert_called_once_with("i")


@pytest.mark.anyio
async def test_browser_observe_allows_skipping_diagnostic_sections(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_pool.get.return_value = _session()
    outline = AsyncMock(return_value={"url": "https://octowright.com", "headings": []})
    console_summary = MagicMock()
    network_summary = MagicMock()
    downloads_summary = MagicMock()
    monkeypatch.setattr(_inspect, "browser_page_outline", outline)
    monkeypatch.setattr(_inspect, "browser_console_summary", console_summary)
    monkeypatch.setattr(_inspect, "browser_network_summary", network_summary)
    monkeypatch.setattr(_inspect, "browser_downloads_summary", downloads_summary)

    out = await _inspect.browser_observe(
        "i",
        include_console=False,
        include_network=False,
        include_downloads=False,
    )

    assert "outline" in out
    assert "console" not in out
    assert "network" not in out
    assert "downloads" not in out
    console_summary.assert_not_called()
    network_summary.assert_not_called()
    downloads_summary.assert_not_called()


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
    assert ev["next_actions"] == [
        {
            "tool": "capture_create",
            "args": {"instance_id": "i", "source": "evaluate", "expression": "x", "response_mode": "summary"},
        },
        {"tool": "browser_evaluate", "args": {"instance_id": "i", "expression": "x", "full": True}},
    ]


@pytest.mark.anyio
async def test_wait_recording_capture_export_and_expects(
    monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock, recordings_dir: Path
) -> None:
    s = _session(recordings_dir)
    _patch_pool.get.return_value = s
    _patch_pool.close = AsyncMock(return_value={"closed": True})
    monkeypatch.setattr(_inspect, "_export_script", MagicMock(return_value=Path("/tmp/out.py")))
    # The raw path reads lines-with-offsets now, so it can stop ON a row when
    # a bound ends the page rather than reporting the window's end.
    monkeypatch.setattr(
        _inspect_recording, "tail_log_lines", MagicMock(return_value=(iter([(0, b'{"a": 1}')]), 10, 10))
    )

    waited = await _inspect.browser_wait_for("i", selector="#a", timeout_ms=1)
    rec_path = _inspect.browser_recording_path("i")
    exported = _inspect.browser_export_script("i", format="python")
    url_ok = await _inspect.browser_expect_url("i", "example")
    text_ok = await _inspect.browser_expect_text("i", "#x", "hello")
    sel_ok = await _inspect.browser_expect_selector("i", "#x", present=False)
    js_ok = await _inspect.browser_expect_js("i", "true")
    tail = _inspect.browser_tail_recording("i")

    assert waited["ok"] is True
    assert rec_path["path"].endswith("rec.jsonl")
    assert exported["path"] == str(Path("/tmp/out.py"))
    assert url_ok["ok"] and text_ok["ok"] and sel_ok["ok"] and js_ok["ok"]
    assert tail["complete"] is True and tail["cursor"] == 10


@pytest.mark.anyio
async def test_browser_expect_text_truncates_large_actual_text(_patch_pool: MagicMock) -> None:
    s = _session()
    s.expect_text = AsyncMock(return_value="x" * 20)
    _patch_pool.get.return_value = s

    out = await _inspect.browser_expect_text("i", "#status", "x", max_chars=5)

    assert out == {
        "ok": True,
        "text": "xxxxx",
        "truncated": True,
        "text_size": 20,
        "cap": 5,
        "next_actions": [
            {
                "tool": "browser_expect_text",
                "args": {"instance_id": "i", "selector": "#status", "text": "x", "full": True},
            }
        ],
    }


@pytest.mark.anyio
async def test_browser_expect_js_truncates_large_result(_patch_pool: MagicMock) -> None:
    s = _session()
    s.expect_js = AsyncMock(return_value={"items": ["x" * 20]})
    _patch_pool.get.return_value = s

    out = await _inspect.browser_expect_js("i", "window.big", max_chars=12)

    assert out == {
        "ok": True,
        "result": '{"items": ["',
        "truncated": True,
        "result_size": 35,
        "cap": 12,
        "next_actions": [
            {"tool": "browser_expect_js", "args": {"instance_id": "i", "expression": "window.big", "full": True}}
        ],
    }


@pytest.mark.anyio
async def test_browser_expect_js_passes_timeout_to_the_session(
    _patch_pool: MagicMock,
) -> None:
    s = _session()
    s.expect_js = AsyncMock(return_value=True)
    _patch_pool.get.return_value = s

    out = await _inspect.browser_expect_js("i", "window.ready", timeout_ms=125_000)

    assert out["ok"] is True
    s.expect_js.assert_awaited_once_with("window.ready", None, timeout_ms=125_000)


@pytest.mark.anyio
async def test_browser_expect_js_full_mode_preserves_result(_patch_pool: MagicMock) -> None:
    s = _session()
    value = {"items": ["x" * 20]}
    s.expect_js = AsyncMock(return_value=value)
    _patch_pool.get.return_value = s

    out = await _inspect.browser_expect_js("i", "window.big", max_chars=12, full=True)

    assert out == {"ok": True, "result": value, "truncated": False, "result_size": 35}


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
async def test_browser_wait_for_outline_mode_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _session()
    _patch_pool.get.return_value = s
    outline = AsyncMock(return_value={"url": "https://octowright.com", "headings": []})
    monkeypatch.setattr(_inspect, "browser_page_outline", outline)

    out = await _inspect.browser_wait_for("i", text="Ready", timeout_ms=500, response_mode="outline")

    assert out["ok"] is True
    assert out["outline"]["url"] == "https://octowright.com"
    s.wait_for.assert_awaited_once_with(None, "Ready", 500, expression=None)
    outline.assert_awaited_once_with("i")


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
    assert out["next_actions"] == [
        {
            "tool": "browser_read_markdown",
            "args": {"instance_id": "i", "response_mode": "summary"},
        },
        {"tool": "browser_read_markdown", "args": {"instance_id": "i", "max_chars": 50}},
        {
            "tool": "capture_create",
            "args": {"instance_id": "i", "source": "markdown", "response_mode": "summary"},
        },
    ]
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


@pytest.mark.anyio
async def test_browser_read_markdown_summary_mode_uses_capture_store(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "core")
    s = _session()
    _patch_pool.get.return_value = s

    md_file = tmp_path / "page-summary.md"
    md_text = "# Overview\n\n" + ("Long paragraph\n" * 300)
    md_file.write_text(md_text)
    s.capture_markdown = AsyncMock(return_value=md_file)

    saved: dict[str, object] = {
        "capture_id": "cap_md",
        "kind": "markdown",
        "size_chars": len(md_text),
        "preview": "# Overview",
        "truncated": True,
    }
    captured_kwargs: dict[str, object] = {}

    def fake_save_capture(**kwargs: object) -> dict[str, object]:
        captured_kwargs.update(kwargs)
        return saved

    def fake_summary(capture_id: str, *, limit: int) -> dict[str, object]:
        return {
            "capture_id": capture_id,
            "outline": [{"line": 1, "kind": "heading", "text": "# Overview"}],
            "limit": limit,
            "next_actions": [{"tool": "capture_lines", "args": {"capture_id": capture_id, "start_line": 1}}],
        }

    monkeypatch.setattr(_inspect._captures, "save_capture", fake_save_capture)
    monkeypatch.setattr(_inspect._captures, "summarize_capture", fake_summary)

    out = await _inspect.browser_read_markdown("i", response_mode="summary", summary_limit=7)

    assert out["capture_id"] == "cap_md"
    assert out["summary"]["outline"][0]["text"] == "# Overview"
    assert out["summary"]["next_actions"] == [
        {
            "tool": "capture_lines",
            "args": {"capture_id": "cap_md", "start_line": 1},
            "available": False,
            "requires_profile": "advanced",
            "available_profiles": ["advanced"],
        }
    ]
    assert out["actions"] == ["capture_summary", "capture_search", "capture_lines", "capture_get"]
    assert out["next_actions"] == [
        {
            "tool": "capture_summary",
            "args": {"capture_id": "cap_md", "limit": 7},
            "available": False,
            "requires_profile": "advanced",
            "available_profiles": ["advanced"],
        },
        {
            "tool": "capture_search",
            "args": {"capture_id": "cap_md", "query": "<query>", "limit": 20},
            "available": False,
            "requires_profile": "advanced",
            "available_profiles": ["advanced"],
        },
        {
            "tool": "capture_lines",
            "args": {"capture_id": "cap_md", "start_line": 1, "limit": 80},
            "available": False,
            "requires_profile": "advanced",
            "available_profiles": ["advanced"],
        },
        {
            "tool": "capture_get",
            "args": {"capture_id": "cap_md", "offset": 0, "limit": _inspect._captures.DEFAULT_SLICE_CHARS},
            "available": False,
            "requires_profile": "advanced",
            "available_profiles": ["advanced"],
        },
    ]
    assert "markdown" not in out
    assert captured_kwargs["kind"] == "markdown"
    assert captured_kwargs["content"] == md_text
    assert captured_kwargs["url"] == "https://octowright.com"
    assert captured_kwargs["title"] == "Example"
    assert captured_kwargs["instance_id"] == "i"
    assert captured_kwargs["source"] == {"source": "markdown", "path": str(md_file)}


def test_top_level_server_exports_browser_read_markdown() -> None:
    from octowright import server

    assert hasattr(server, "browser_read_markdown")
    assert hasattr(server, "browser_console_summary")
    assert hasattr(server, "browser_fields")
    assert hasattr(server, "browser_find_field")
    assert hasattr(server, "browser_links")
    assert hasattr(server, "browser_find_link")
    assert hasattr(server, "browser_page_outline")
    assert hasattr(server, "browser_observe")


@pytest.mark.anyio
async def test_browser_capture_and_close_with_snapshot(capture_pool: BrowserPool, recordings_dir: Path) -> None:
    s = _capture_session(recordings_dir)
    capture_pool._sessions["i"] = s
    out = await _inspect_capture.browser_capture_and_close("i", snapshot=True)
    assert out["closed"] is True
    assert out["aria"] == "aria-content"
    s._teardown_after_close_cutoff.assert_awaited_once()


@pytest.mark.anyio
async def test_browser_capture_and_close_uses_active_frame(capture_pool: BrowserPool, recordings_dir: Path) -> None:
    """With a frame active, the captured aria + url come from the frame, not the top page."""
    s = _capture_session(recordings_dir)
    frame = MagicMock()
    frame.url = "https://widget.octowright.com/inner"
    frame.locator.return_value.aria_snapshot = AsyncMock(return_value="frame-html-aria")
    stub_credential_scan(frame.locator.return_value)
    s._target.return_value = frame
    capture_pool._sessions["i"] = s

    out = await _inspect_capture.browser_capture_and_close("i", snapshot=True)

    assert out["url"] == "https://widget.octowright.com/inner"
    assert out["aria"] == "frame-html-aria"
    # title stays page-level.
    assert out["title"] == "Example"


@pytest.mark.anyio
async def test_browser_capture_and_close_snapshot_timeout_still_closes(
    capture_pool: BrowserPool,
    recordings_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _capture_session(recordings_dir)

    async def slow_snapshot() -> str:
        await asyncio.sleep(10)
        return "late aria"

    s.page.locator.return_value.aria_snapshot = AsyncMock(side_effect=slow_snapshot)
    stub_credential_scan(s.page.locator.return_value)
    capture_pool._sessions["i"] = s
    monkeypatch.setattr(_inspect_capture, "SNAPSHOT_TIMEOUT_S", 0.01)

    out = await _inspect_capture.browser_capture_and_close("i", snapshot=True)

    assert out["closed"] is True
    assert out["snapshot_timed_out"] is True
    assert out["timeout_s"] == 0.01
    assert "aria" not in out
    assert out["actions"] == [
        {"tool": "browser_page_outline", "args": {"instance_id": "i"}},
        {"tool": "browser_read_markdown", "args": {"instance_id": "i", "response_mode": "summary"}},
        {"tool": "browser_snapshot", "args": {"instance_id": "i", "selector": "main"}},
    ]
    s.screenshot.assert_awaited_once()
    s._teardown_after_close_cutoff.assert_awaited_once()


@pytest.mark.anyio
async def test_browser_capture_and_close_and_closed(capture_pool: BrowserPool, recordings_dir: Path) -> None:
    """The `cap = await _inspect.browser_capture_and_close(...)` slice split
    out of test_wait_recording_capture_export_and_expects: that combined
    test's session/pool are fully mocked (no real gate), which capture-and-
    close's Task 8 preparation-at-ticket machinery can no longer accept."""
    s = _capture_session(recordings_dir)
    capture_pool._sessions["i"] = s

    cap = await _inspect_capture.browser_capture_and_close("i", snapshot=False)

    assert cap["closed"] is True


@pytest.mark.anyio
async def test_capture_and_close_preparation_runs_at_close_ticket(
    capture_pool: BrowserPool, recordings_dir: Path
) -> None:
    """A navigation racing the close ticket must be captured by the
    preparation callback -- the reported url reflects the FINAL navigated
    url (read only once the ticket owns the gate), not a pre-close read a
    concurrent navigation could have raced past. A later manual op attempted
    after the ticket is accepted is rejected with SessionClosingError."""
    s = _capture_session(recordings_dir)
    capture_pool._sessions["i"] = s

    release_navigation = asyncio.Event()

    async def _navigate() -> None:
        async with s.operation("browser_navigate"):
            s.page.url = "https://final.test"
            await release_navigation.wait()

    navigation = asyncio.create_task(_navigate())
    await wait_for_active(s._operation_gate, "browser_navigate")

    capture = asyncio.create_task(_inspect_capture.browser_capture_and_close("i", force=True))
    await wait_for_state(s._operation_gate, "closing")
    with pytest.raises(SessionClosingError):
        async with s.operation("late_action"):
            pass

    release_navigation.set()
    await navigation
    result = await capture
    assert result["url"] == "https://final.test"
    assert result["closed"] is True


@pytest.mark.anyio
async def test_browser_read_markdown_url_uses_active_frame(_patch_pool: MagicMock, tmp_path: Path) -> None:
    """read_markdown reports the frame's url when a frame is active (content already is)."""
    s = _session()
    frame = MagicMock()
    frame.url = "https://widget.octowright.com/inner"
    s._target.return_value = frame
    md_file = tmp_path / "page.md"
    md_file.write_text("Frame Markdown")
    s.capture_markdown = AsyncMock(return_value=md_file)
    _patch_pool.get.return_value = s

    out = await _inspect.browser_read_markdown("i")

    assert out["url"] == "https://widget.octowright.com/inner"


@pytest.mark.anyio
async def test_browser_capture_and_close_refuses_protected_before_side_effects(
    capture_pool: BrowserPool,
    recordings_dir: Path,
) -> None:
    s = _capture_session(recordings_dir)
    s.protected = True
    capture_pool._sessions["i"] = s

    out = await _inspect_capture.browser_capture_and_close("i", snapshot=True)

    assert "error" in out
    assert "force=True" in out["error"]
    s.screenshot.assert_not_awaited()
    s.page.locator.assert_not_called()
    s._teardown_after_close_cutoff.assert_not_awaited()
    # The protection check IS the reservation preflight: a refusal never
    # even reserves the close cutoff, so the session stays fully live.
    assert "i" in capture_pool._sessions
    assert s.operation_snapshot()["state"] == "open"


@pytest.mark.anyio
async def test_browser_capture_and_close_force_closes_protected(
    capture_pool: BrowserPool,
    recordings_dir: Path,
) -> None:
    s = _capture_session(recordings_dir)
    s.protected = True
    capture_pool._sessions["i"] = s

    out = await _inspect_capture.browser_capture_and_close("i", snapshot=False, force=True)

    assert out["closed"] is True
    s.screenshot.assert_awaited_once()
    s._teardown_after_close_cutoff.assert_awaited_once()


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
    capture_pool: BrowserPool, recordings_dir: Path, tmp_path: Path
) -> None:
    """browser_capture_and_close also confines screenshot_path -- pure
    validation that runs BEFORE any close reservation, so a rejected path
    has zero side effects (the session is never touched)."""
    s = _capture_session(recordings_dir)
    capture_pool._sessions["i"] = s
    outside = tmp_path.parent / "escape" / "evil.png"
    with pytest.raises(ValueError, match="resolves outside"):
        await _inspect_capture.browser_capture_and_close("i", screenshot_path=str(outside))
    s.screenshot.assert_not_called()
    assert "i" in capture_pool._sessions
    assert s.operation_snapshot()["state"] == "open"


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
async def test_browser_read_markdown_names_a_hung_target(_patch_pool: MagicMock) -> None:
    """capture_markdown() swallows its own exceptions and always returns
    None on failure, so a hung target and a missing dependency look
    identical unless the tool consults the recorded cause. When it was a
    SessionCallTimeoutError, the tool must say so -- "ensure markitdown is
    installed" is actively misleading for a target that simply stopped
    answering."""
    from octowright.session.timeouts import SessionCallTimeoutError

    s = _session()
    _patch_pool.get.return_value = s
    s.markdown_path = None
    s.capture_markdown = AsyncMock(return_value=None)
    s._last_markdown_capture_error = SessionCallTimeoutError(
        "markdown_capture did not answer within 10.0s -- the browser target is "
        "unresponsive. Relaunch this session; other sessions are unaffected."
    )
    with pytest.raises(SessionCallTimeoutError, match="unresponsive"):
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

    from octowright.recorder import Recorder
    from octowright.session.core import BrowserSession

    log_path = tmp_path / "rec.jsonl"
    recorder = Recorder(log_path)

    page: Any = SimpleNamespace(url="https://example.com")
    page.title = AsyncMock(return_value="Ex")
    page.locator = MagicMock()
    page.locator.return_value.aria_snapshot = AsyncMock(return_value="- main")
    stub_credential_scan(page.locator.return_value)

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


@pytest.mark.anyio
async def test_browser_brief_uses_frame_when_active(_patch_pool: MagicMock) -> None:
    """brief must reflect the switched frame, like snapshot — not the top page."""
    s = _session()
    frame = MagicMock()
    frame.url = "https://widget.octowright.com"
    frame.locator.return_value.aria_snapshot = AsyncMock(return_value="frame-body-aria")
    stub_credential_scan(frame.locator.return_value)
    s._target.return_value = frame  # simulate an active frame
    _patch_pool.get.return_value = s
    out = await _inspect.browser_brief("i")

    assert out["url"] == "https://widget.octowright.com"  # frame url, not page
    assert out["title"] == "Example"  # title stays page-level
    assert "frame-body-aria" in out["elements"]


@pytest.mark.anyio
async def test_browser_links_returns_compact_candidates(_patch_pool: MagicMock) -> None:
    s = _session()
    s.page.evaluate = AsyncMock(
        return_value=[
            {
                "text": "Pricing",
                "href": "https://octowright.com/pricing",
                "role": "link",
                "label": "Pricing",
                "selector": "a[href='/pricing']",
                "visible": True,
            },
            {
                "text": "Docs",
                "href": "https://octowright.com/docs",
                "role": "link",
                "label": "Documentation",
                "selector": "a[href='/docs']",
                "visible": True,
            },
        ]
    )
    _patch_pool.get.return_value = s

    out = await _inspect.browser_links("i", limit=1)

    assert out["url"] == "https://octowright.com"
    assert out["title"] == "Example"
    assert out["truncated"] is True
    assert out["total"] == 2
    assert out["links"] == [
        {
            "text": "Pricing",
            "href": "https://octowright.com/pricing",
            "role": "link",
            "label": "Pricing",
            "selector": "a[href='/pricing']",
            "visible": True,
            "action": {
                "tool": "browser_click",
                "args": {
                    "instance_id": "i",
                    "role": "link",
                    "role_name": "Pricing",
                    "response_mode": "outline",
                },
                "fallback_args": {
                    "instance_id": "i",
                    "selector": "a[href='/pricing']",
                    "response_mode": "outline",
                },
            },
        }
    ]
    assert out["next_actions"] == [
        {"tool": "browser_find_link", "args": {"instance_id": "i", "query": "<intent>", "limit": 8}},
        {"tool": "browser_page_outline", "args": {"instance_id": "i", "limit": 20}},
    ]
    assert s.page.locator.return_value.aria_snapshot.await_count == 0


@pytest.mark.anyio
async def test_browser_find_link_scores_query(_patch_pool: MagicMock) -> None:
    s = _session()
    s.page.evaluate = AsyncMock(
        return_value=[
            {
                "text": "Docs",
                "href": "https://octowright.com/docs",
                "role": "link",
                "label": "Documentation",
                "selector": "a[href='/docs']",
                "visible": True,
            },
            {
                "text": "Pricing",
                "href": "https://octowright.com/pricing",
                "role": "link",
                "label": "Plans and pricing",
                "selector": "a[href='/pricing']",
                "visible": True,
            },
        ]
    )
    _patch_pool.get.return_value = s

    out = await _inspect.browser_find_link("i", "pricing", limit=1)

    assert out["query"] == "pricing"
    assert out["links"][0]["href"] == "https://octowright.com/pricing"
    assert out["links"][0]["action"]["args"] == {
        "instance_id": "i",
        "role": "link",
        "role_name": "Plans and pricing",
        "response_mode": "outline",
    }
    assert out["links"][0]["score"] > out["links"][0]["rank"]
    assert "label contains query" in out["links"][0]["reason"]


@pytest.mark.anyio
async def test_browser_fields_returns_compact_candidates(_patch_pool: MagicMock) -> None:
    s = _session()
    s.page.evaluate = AsyncMock(
        return_value=[
            {
                "name": "email",
                "type": "email",
                "label": "Email address",
                "placeholder": "you@example.com",
                "selector": "#email",
                "required": True,
                "visible": True,
            },
            {
                "name": "plan",
                "tag": "select",
                "label": "Plan",
                "selector": "select[name='plan']",
                "required": False,
                "visible": True,
            },
        ]
    )
    _patch_pool.get.return_value = s

    out = await _inspect.browser_fields("i", limit=1)

    assert out["url"] == "https://octowright.com"
    assert out["title"] == "Example"
    assert out["truncated"] is True
    assert out["total"] == 2
    assert out["fields"] == [
        {
            "name": "email",
            "type": "email",
            "label": "Email address",
            "placeholder": "you@example.com",
            "selector": "#email",
            "required": True,
            "visible": True,
            "action": {
                "tool": "browser_fill",
                "args": {"instance_id": "i", "label": "Email address", "response_mode": "outline"},
                "fallback_args": {"instance_id": "i", "selector": "#email", "response_mode": "outline"},
                "requires_args": ["value"],
            },
        }
    ]
    assert out["next_actions"] == [
        {"tool": "browser_find_field", "args": {"instance_id": "i", "query": "<intent>", "limit": 8}},
        {"tool": "browser_page_outline", "args": {"instance_id": "i", "limit": 20}},
    ]
    assert s.page.locator.return_value.aria_snapshot.await_count == 0


@pytest.mark.anyio
async def test_browser_find_field_scores_query(_patch_pool: MagicMock) -> None:
    s = _session()
    s.page.evaluate = AsyncMock(
        return_value=[
            {
                "name": "q",
                "type": "search",
                "label": "Search docs",
                "placeholder": "Search",
                "selector": "input[name='q']",
                "visible": True,
            },
            {
                "name": "email",
                "type": "email",
                "label": "Work email",
                "placeholder": "you@example.com",
                "selector": "#email",
                "visible": True,
            },
        ]
    )
    _patch_pool.get.return_value = s

    out = await _inspect.browser_find_field("i", "email", limit=1)

    assert out["query"] == "email"
    assert out["fields"][0]["selector"] == "#email"
    assert out["fields"][0]["action"]["args"] == {
        "instance_id": "i",
        "label": "Work email",
        "response_mode": "outline",
    }
    assert out["fields"][0]["action"]["requires_args"] == ["value"]
    assert out["fields"][0]["score"] > out["fields"][0]["rank"]
    assert "label contains query" in out["fields"][0]["reason"]


@pytest.mark.anyio
async def test_browser_page_outline_returns_compact_dom_map(_patch_pool: MagicMock) -> None:
    s = _session()
    s.page.evaluate = AsyncMock(
        return_value={
            "headings": [
                {
                    "level": 1,
                    "text": "Account settings",
                    "selector": "h1",
                    "visible": True,
                }
            ],
            "landmarks": [
                {
                    "role": "navigation",
                    "text": "Docs Pricing",
                    "selector": "nav",
                    "visible": True,
                },
                {
                    "role": "main",
                    "text": "Account settings Work email",
                    "selector": "main",
                    "visible": True,
                },
            ],
            "links": [
                {
                    "text": "Pricing",
                    "href": "https://octowright.com/pricing",
                    "role": "link",
                    "selector": "a[href='/pricing']",
                    "visible": True,
                }
            ],
            "fields": [
                {
                    "name": "email",
                    "type": "email",
                    "label": "Work email",
                    "selector": "#email",
                    "required": True,
                    "visible": True,
                }
            ],
            "counts": {"headings": 1, "landmarks": 2, "links": 1, "fields": 1},
        }
    )
    _patch_pool.get.return_value = s

    out = await _inspect.browser_page_outline("i", limit=1)

    assert out["url"] == "https://octowright.com"
    assert out["title"] == "Example"
    assert out["headings"] == [
        {
            "level": 1,
            "text": "Account settings",
            "selector": "h1",
            "visible": True,
        }
    ]
    assert out["landmarks"] == [
        {
            "role": "navigation",
            "text": "Docs Pricing",
            "selector": "nav",
            "visible": True,
        }
    ]
    assert out["links"][0]["href"] == "https://octowright.com/pricing"
    assert out["links"][0]["action"]["args"] == {
        "instance_id": "i",
        "role": "link",
        "role_name": "Pricing",
        "response_mode": "outline",
    }
    assert out["fields"][0]["selector"] == "#email"
    assert out["fields"][0]["action"]["args"] == {
        "instance_id": "i",
        "label": "Work email",
        "response_mode": "outline",
    }
    assert out["fields"][0]["action"]["requires_args"] == ["value"]
    assert out["counts"] == {"headings": 1, "landmarks": 2, "links": 1, "fields": 1}
    assert out["truncated"] is True
    assert out["next_actions"] == [
        {"tool": "browser_find_link", "args": {"instance_id": "i", "query": "<intent>", "limit": 8}},
        {"tool": "browser_find_field", "args": {"instance_id": "i", "query": "<intent>", "limit": 8}},
        {"tool": "browser_read_markdown", "args": {"instance_id": "i", "response_mode": "summary"}},
        {"tool": "capture_create", "args": {"instance_id": "i", "source": "snapshot", "response_mode": "summary"}},
    ]
    assert s.page.locator.return_value.aria_snapshot.await_count == 0


@pytest.mark.anyio
async def test_browser_page_outline_handles_aria_heading_without_level(_patch_pool: MagicMock) -> None:
    s = _session()
    s.page.evaluate = AsyncMock(
        return_value={
            "headings": [{"level": math.nan, "text": "Untyped heading", "selector": "div", "visible": True}],
            "landmarks": [],
            "links": [],
            "fields": [],
            "counts": {"headings": 1, "landmarks": 0, "links": 0, "fields": 0},
        }
    )
    _patch_pool.get.return_value = s

    out = await _inspect.browser_page_outline("i", limit=10)

    assert out["headings"] == [{"level": 0, "text": "Untyped heading", "selector": "div", "visible": True}]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
