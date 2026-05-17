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
    return s


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
async def test_capture_create_evaluate_requires_expression(tmp_path: Path, _patch_pool: MagicMock) -> None:
    _patch_pool.get.return_value = _session(tmp_path)
    with pytest.raises(ValueError, match="expression is required"):
        await _tools.capture_create("abc", source="evaluate")
