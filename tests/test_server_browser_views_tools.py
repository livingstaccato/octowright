# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import views as _views


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    fake_pool = MagicMock()
    monkeypatch.setattr(_views, "pool", fake_pool)
    return fake_pool


def test_browser_downloads_summary_aggregates_without_raw_dump(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.list_downloads.return_value = [
        {
            "url": "https://example.com/reports/a.csv",
            "suggested_filename": "a.csv",
            "path": "/tmp/a.csv",
            "ts": "2026-01-01T00:00:00Z",
        },
        {
            "url": "https://example.com/reports/b.csv",
            "suggested_filename": "b.csv",
            "path": "/tmp/b.csv",
            "ts": "2026-01-01T00:00:01Z",
        },
        {
            "url": "https://cdn.example.com/manual.pdf",
            "suggested_filename": "manual.pdf",
            "path": "/tmp/manual.pdf",
            "ts": "2026-01-01T00:00:02Z",
        },
    ]
    _patch_pool.get.return_value = session

    out = _views.browser_downloads_summary("i", recent_limit=2)

    assert out["total"] == 3
    assert out["count"] == 3
    assert out["next_cursor"] == 3
    assert out["by_extension"] == [{"key": ".csv", "count": 2}, {"key": ".pdf", "count": 1}]
    assert out["by_host"] == [{"key": "example.com", "count": 2}, {"key": "cdn.example.com", "count": 1}]
    assert out["recent"] == [
        {
            "index": 1,
            "suggested_filename": "b.csv",
            "url": "https://example.com/reports/b.csv",
            "path": "/tmp/b.csv",
            "action": {"tool": "browser_downloads_summary", "args": {"instance_id": "i", "after": 1}},
        },
        {
            "index": 2,
            "suggested_filename": "manual.pdf",
            "url": "https://cdn.example.com/manual.pdf",
            "path": "/tmp/manual.pdf",
            "action": {"tool": "browser_downloads_summary", "args": {"instance_id": "i", "after": 2}},
        },
    ]
    assert out["next_actions"] == [
        {"tool": "browser_downloads_summary", "args": {"instance_id": "i", "after": 3}},
        {"tool": "browser_wait_for_download", "args": {"instance_id": "i"}},
    ]
    assert "downloads" not in out


def test_browser_downloads_summary_mode_delegates_to_compact_summary(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.list_downloads.return_value = [
        {"suggested_filename": "old.txt", "url": "https://example.com/old.txt"},
        {"suggested_filename": "report.csv", "url": "https://example.com/report.csv"},
    ]
    _patch_pool.get.return_value = session

    out = _views.browser_downloads("i", after=1, response_mode="summary")

    assert "downloads" not in out
    assert out["count"] == 1
    assert out["by_extension"] == [{"key": ".csv", "count": 1}]
    assert out["recent"][0]["suggested_filename"] == "report.csv"


def test_page_list_summary_bounds_rows_and_adds_actions(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.list_pages.return_value = [
        {"index": 0, "url": "https://example.com/" + ("a" * 300), "title": "Home" + ("!" * 300), "is_active": True},
        {"index": 1, "url": "https://example.com/docs", "title": "Docs", "is_active": False},
        {"index": 2, "url": "https://example.com/login", "title": "Login", "is_active": False},
    ]
    _patch_pool.get.return_value = session

    out = _views.page_list("i", response_mode="summary", limit=2)

    assert out["total"] == 3
    assert out["count"] == 2
    assert out["truncated"] is True
    assert out["pages"][0]["index"] == 0
    assert len(out["pages"][0]["url"]) == 200
    assert len(out["pages"][0]["title"]) == 120
    assert out["pages"][0]["action"] == {
        "tool": "page_switch",
        "args": {"instance_id": "i", "index": 0, "response_mode": "outline"},
    }
    assert out["pages"][1]["actions"] == [
        {"tool": "page_switch", "args": {"instance_id": "i", "index": 1, "response_mode": "outline"}},
        {"tool": "page_close", "args": {"instance_id": "i", "index": 1}},
    ]


def test_browser_list_frames_summary_bounds_rows_and_adds_actions(_patch_pool: MagicMock) -> None:
    session = MagicMock()
    session.list_frames.return_value = [
        {
            "index": 0,
            "name": "",
            "url": "https://example.com/top",
            "is_active": True,
            "is_main": True,
        },
        {
            "index": 1,
            "name": "checkout",
            "url": "https://pay.example.com/frame?" + ("q" * 300),
            "is_active": False,
            "is_main": False,
            "selector": "iframe[name='checkout']",
        },
    ]
    _patch_pool.get.return_value = session

    out = _views.browser_list_frames("i", response_mode="summary", limit=2)

    assert out["total"] == 2
    assert out["count"] == 2
    assert out["frames"][0]["action"] == {
        "tool": "browser_reset_frame",
        "args": {"instance_id": "i", "response_mode": "outline"},
    }
    assert out["frames"][1]["url"].endswith("q")
    assert len(out["frames"][1]["url"]) == 200
    assert out["frames"][1]["action"] == {
        "tool": "browser_switch_frame",
        "args": {"instance_id": "i", "selector": "iframe[name='checkout']", "response_mode": "outline"},
    }


@pytest.mark.anyio
async def test_page_switch_outline_mode_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = MagicMock()
    session.switch_page = AsyncMock(return_value={"ok": True, "index": 1})
    _patch_pool.get.return_value = session
    outline = AsyncMock(return_value={"url": "https://example.com/docs", "headings": []})
    monkeypatch.setattr(_views, "browser_page_outline", outline)

    out = await _views.page_switch("i", 1, response_mode="outline")

    assert out["outline"]["url"] == "https://example.com/docs"
    session.switch_page.assert_awaited_once_with(1)
    outline.assert_awaited_once_with("i")


@pytest.mark.anyio
async def test_browser_switch_frame_outline_mode_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = MagicMock()
    session.switch_frame = AsyncMock(return_value={"ok": True, "index": 2})
    _patch_pool.get.return_value = session
    outline = AsyncMock(return_value={"url": "https://widget.example.com", "fields": []})
    monkeypatch.setattr(_views, "browser_page_outline", outline)

    out = await _views.browser_switch_frame("i", selector="iframe.checkout", response_mode="outline")

    assert out["outline"]["url"] == "https://widget.example.com"
    session.switch_frame.assert_awaited_once_with(selector="iframe.checkout", name=None, url_pattern=None)
    outline.assert_awaited_once_with("i")


@pytest.mark.anyio
async def test_browser_reset_frame_outline_mode_returns_page_outline(
    _patch_pool: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = MagicMock()
    session.reset_frame = AsyncMock(return_value={"ok": True})
    _patch_pool.get.return_value = session
    outline = AsyncMock(return_value={"url": "https://example.com", "links": []})
    monkeypatch.setattr(_views, "browser_page_outline", outline)

    out = await _views.browser_reset_frame("i", response_mode="outline")

    assert out["outline"]["url"] == "https://example.com"
    session.reset_frame.assert_awaited_once_with()
    outline.assert_awaited_once_with("i")


def test_top_level_server_exports_browser_downloads_summary() -> None:
    from octowright import server

    assert hasattr(server, "browser_downloads_summary")
