# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import input as _input


@pytest.fixture(autouse=True)
def _patch_pool_input(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_pool = MagicMock()
    monkeypatch.setattr(_input, "pool", fake_pool)
    return fake_pool


@pytest.mark.anyio
async def test_browser_click_brief_mode(_patch_pool_input: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    s = MagicMock()
    _patch_pool_input.get.return_value = s
    s.click = AsyncMock(return_value={"ok": True})

    monkeypatch.setattr(_input, "browser_brief", AsyncMock(return_value={"url": "test", "elements": "none"}))

    out = await _input.browser_click("i", "button", response_mode="brief")
    assert out["brief"]["url"] == "test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
