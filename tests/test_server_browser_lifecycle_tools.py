# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import lifecycle as _lifecycle


@pytest.fixture(autouse=True)
def _patch_pool_lifecycle(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_pool = MagicMock()
    monkeypatch.setattr(_lifecycle, "pool", fake_pool)
    return fake_pool


@pytest.mark.anyio
async def test_browser_navigate_default_returns_navigate_result(
    _patch_pool_lifecycle: MagicMock,
) -> None:
    s = MagicMock()
    _patch_pool_lifecycle.get.return_value = s
    s.navigate = AsyncMock(return_value={"url": "https://example.com", "title": "Example"})

    out = await _lifecycle.browser_navigate("i", "https://example.com")
    assert out == {"url": "https://example.com", "title": "Example"}
    assert "brief" not in out


@pytest.mark.anyio
async def test_browser_navigate_brief_mode_includes_brief(
    _patch_pool_lifecycle: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = MagicMock()
    _patch_pool_lifecycle.get.return_value = s
    s.navigate = AsyncMock(return_value={"url": "https://example.com", "title": "Example"})

    monkeypatch.setattr(
        _lifecycle,
        "browser_brief",
        AsyncMock(return_value={"url": "https://example.com", "title": "Example", "elements": "..."}),
    )

    out = await _lifecycle.browser_navigate("i", "https://example.com", response_mode="brief")
    assert out["url"] == "https://example.com"
    assert out["title"] == "Example"
    assert out["brief"]["elements"] == "..."


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
