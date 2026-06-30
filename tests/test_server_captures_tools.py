# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server import captures as _tools


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    fake_pool = MagicMock()
    monkeypatch.setattr(_tools, "pool", fake_pool)
    return fake_pool


def _session(tmp_path: Path) -> MagicMock:
    s = MagicMock()
    s.page.url = "https://warp.undef.games/customize"
    s.page.title = AsyncMock(return_value="Warp")
    s.page.locator.return_value.aria_snapshot = AsyncMock(return_value='- button "Save"')
    s.page.locator.return_value.inner_text = AsyncMock(return_value="Enter your alias")
    s.evaluate = AsyncMock(return_value={"ok": True})
    s.console = [{"level": "error", "text": "boom"}]
    s.get_network_requests = MagicMock(return_value={"requests": [{"url": "https://example.test"}]})
    s.capture_markdown = AsyncMock(return_value=tmp_path / "page.md")
    (tmp_path / "page.md").write_text("# Page")
    s.log_path = tmp_path / "session.jsonl"
    s.log_path.write_text('{"action":"launch"}\n')
    # _target() defaults to the page when no frame is switched.
    s._target.return_value = s.page
    return s


def _frame() -> MagicMock:
    """A switched iframe with its own aria/text/url."""
    f = MagicMock()
    f.url = "https://widget.undef.games/inner"
    f.locator.return_value.aria_snapshot = AsyncMock(return_value='- button "Frame Save"')
    f.locator.return_value.inner_text = AsyncMock(return_value="Frame alias field")
    return f


@pytest.mark.anyio
async def test_capture_create_snapshot_uses_capture_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock
) -> None:
    s = _session(tmp_path)
    _patch_pool.get.return_value = s
    captured: dict[str, object] = {}

    def fake_save(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"capture_id": "cap_test", "preview": "preview", "truncated": False}

    monkeypatch.setattr(_tools._captures, "save_capture", fake_save)

    out = await _tools.capture_create("abc", source="snapshot")

    assert out["capture_id"] == "cap_test"
    assert captured["content"] == '- button "Save"'
    assert captured["url"] == "https://warp.undef.games/customize"


@pytest.mark.anyio
async def test_capture_create_summary_mode_returns_compact_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock
) -> None:
    s = _session(tmp_path)
    _patch_pool.get.return_value = s

    monkeypatch.setattr(
        _tools._captures,
        "save_capture",
        lambda **_kw: {"capture_id": "cap_test", "preview": "- button", "truncated": False},
    )

    def fake_summary(capture_id: str, *, limit: int) -> dict[str, object]:
        return {
            "capture_id": capture_id,
            "returned": 1,
            "outline": [{"line": 1, "kind": "aria", "text": '- button "Save"'}],
            "limit": limit,
        }

    monkeypatch.setattr(_tools._captures, "summarize_capture", fake_summary)

    out = await _tools.capture_create("abc", source="snapshot", response_mode="summary", summary_limit=9)

    assert out["capture_id"] == "cap_test"
    assert out["summary"]["outline"][0]["text"] == '- button "Save"'
    assert out["summary"]["limit"] == 9
    assert out["actions"] == ["capture_summary", "capture_search", "capture_lines", "capture_get"]
    assert out["next_actions"] == [
        {"tool": "capture_summary", "args": {"capture_id": "cap_test", "limit": 9}},
        {"tool": "capture_search", "args": {"capture_id": "cap_test", "query": "<query>", "limit": 20}},
        {"tool": "capture_lines", "args": {"capture_id": "cap_test", "start_line": 1, "limit": 80}},
        {
            "tool": "capture_get",
            "args": {"capture_id": "cap_test", "offset": 0, "limit": _tools._captures.DEFAULT_SLICE_CHARS},
        },
    ]


@pytest.mark.anyio
async def test_capture_create_snapshot_uses_active_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock
) -> None:
    """A snapshot capture taken while a frame is active must read the frame, not the top page."""
    s = _session(tmp_path)
    s._target.return_value = _frame()
    _patch_pool.get.return_value = s
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        _tools._captures,
        "save_capture",
        lambda **kw: captured.update(kw) or {"capture_id": "c", "preview": "", "truncated": False},
    )

    await _tools.capture_create("abc", source="snapshot")

    assert captured["content"] == '- button "Frame Save"'
    assert captured["url"] == "https://widget.undef.games/inner"


@pytest.mark.anyio
async def test_capture_create_text_uses_active_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock
) -> None:
    """A text capture while a frame is active reads the frame body text."""
    s = _session(tmp_path)
    s._target.return_value = _frame()
    _patch_pool.get.return_value = s
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        _tools._captures,
        "save_capture",
        lambda **kw: captured.update(kw) or {"capture_id": "c", "preview": "", "truncated": False},
    )

    await _tools.capture_create("abc", source="text")

    assert captured["content"] == "Frame alias field"
    assert captured["url"] == "https://widget.undef.games/inner"


@pytest.mark.anyio
async def test_capture_create_evaluate_requires_expression(tmp_path: Path, _patch_pool: MagicMock) -> None:
    _patch_pool.get.return_value = _session(tmp_path)
    with pytest.raises(ValueError, match="expression is required"):
        await _tools.capture_create("abc", source="evaluate")


def test_capture_summary_delegates_to_capture_store(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_summary(capture_id: str, *, limit: int) -> dict[str, object]:
        captured["capture_id"] = capture_id
        captured["limit"] = limit
        return {"capture_id": capture_id, "outline": []}

    monkeypatch.setattr(_tools._captures, "summarize_capture", fake_summary)

    out = _tools.capture_summary("cap_123", limit=7)

    assert out == {"capture_id": "cap_123", "outline": []}
    assert captured == {"capture_id": "cap_123", "limit": 7}


def test_capture_summary_annotates_nested_next_actions_for_active_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "core")

    monkeypatch.setattr(
        _tools._captures,
        "summarize_capture",
        lambda _capture_id, *, limit: {
            "capture_id": "cap_123",
            "outline": [],
            "next_actions": [{"tool": "capture_get", "args": {"capture_id": "cap_123", "offset": 0}}],
        },
    )

    out = _tools.capture_summary("cap_123")

    assert out["next_actions"] == [
        {
            "tool": "capture_get",
            "args": {"capture_id": "cap_123", "offset": 0},
            "available": False,
            "requires_profile": "advanced",
            "available_profiles": ["advanced"],
        }
    ]


@pytest.mark.anyio
async def test_capture_create_summary_annotates_inline_summary_next_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _patch_pool: MagicMock
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "core")
    s = _session(tmp_path)
    _patch_pool.get.return_value = s
    monkeypatch.setattr(
        _tools._captures,
        "save_capture",
        lambda **_kw: {"capture_id": "cap_test", "preview": "preview", "truncated": False},
    )
    monkeypatch.setattr(
        _tools._captures,
        "summarize_capture",
        lambda _capture_id, *, limit: {
            "capture_id": "cap_test",
            "outline": [],
            "next_actions": [{"tool": "capture_lines", "args": {"capture_id": "cap_test", "start_line": 1}}],
        },
    )

    out = await _tools.capture_create("abc", source="snapshot", response_mode="summary")

    assert out["summary"]["next_actions"] == [
        {
            "tool": "capture_lines",
            "args": {"capture_id": "cap_test", "start_line": 1},
            "available": False,
            "requires_profile": "advanced",
            "available_profiles": ["advanced"],
        }
    ]
    assert out["next_actions"][0]["tool"] == "capture_summary"
    assert out["next_actions"][0]["available"] is False


def test_capture_lines_delegates_to_capture_store(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_lines(capture_id: str, *, start_line: int, limit: int) -> dict[str, object]:
        captured["capture_id"] = capture_id
        captured["start_line"] = start_line
        captured["limit"] = limit
        return {"capture_id": capture_id, "lines": []}

    monkeypatch.setattr(_tools._captures, "get_capture_lines", fake_lines)

    out = _tools.capture_lines("cap_123", start_line=8, limit=9)

    assert out == {"capture_id": "cap_123", "lines": []}
    assert captured == {"capture_id": "cap_123", "start_line": 8, "limit": 9}


def test_top_level_server_exports_capture_summary() -> None:
    from octowright import server

    assert hasattr(server, "capture_summary")
    assert hasattr(server, "capture_lines")
