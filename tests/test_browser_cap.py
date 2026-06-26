# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""OCTOWRIGHT_MAX_BROWSERS pool-wide cap enforcement (server/browser/lifecycle)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import defaults as _defaults
from octowright.browser_pool.errors import BrowserCapExceededError
from octowright.server.browser import lifecycle as _lifecycle


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    pool = MagicMock()
    monkeypatch.setattr(_lifecycle, "pool", pool)
    return pool


def _set_cap(monkeypatch: pytest.MonkeyPatch, value: int | None) -> None:
    monkeypatch.setattr(_defaults, "MAX_BROWSERS", value)


def test_enforce_cap_off_never_raises(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    _set_cap(monkeypatch, None)
    fake_pool.active_count.return_value = 999
    _lifecycle._enforce_browser_cap(adding=50)  # no raise


def test_enforce_cap_under_limit_ok(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    _set_cap(monkeypatch, 5)
    fake_pool.active_count.return_value = 3
    _lifecycle._enforce_browser_cap(adding=2)  # 3 + 2 == 5, allowed


def test_enforce_cap_over_limit_raises(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    _set_cap(monkeypatch, 5)
    fake_pool.active_count.return_value = 5
    with pytest.raises(BrowserCapExceededError, match="OCTOWRIGHT_MAX_BROWSERS=5"):
        _lifecycle._enforce_browser_cap(adding=1)


@pytest.mark.anyio
async def test_browser_launch_refuses_at_cap(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    _set_cap(monkeypatch, 2)
    fake_pool.active_count.return_value = 2
    fake_pool.launch = AsyncMock()
    with pytest.raises(BrowserCapExceededError):
        await _lifecycle.browser_launch(ephemeral=True)
    # The cap fires before any launch attempt.
    fake_pool.launch.assert_not_awaited()


@pytest.mark.anyio
async def test_spawn_roster_refuses_when_batch_exceeds(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    _set_cap(monkeypatch, 4)
    fake_pool.active_count.return_value = 3
    fake_pool.spawn_roster = AsyncMock()
    with pytest.raises(BrowserCapExceededError):
        await _lifecycle.browser_spawn_roster([{"kind": "chromium"}, {"kind": "firefox"}])
    fake_pool.spawn_roster.assert_not_awaited()


@pytest.mark.anyio
async def test_spawn_roster_allows_when_batch_fits(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    _set_cap(monkeypatch, 4)
    fake_pool.active_count.return_value = 2
    fake_pool.spawn_roster = AsyncMock(return_value={"launched": [], "errors": []})
    out = await _lifecycle.browser_spawn_roster([{"kind": "chromium"}, {"kind": "firefox"}])
    assert out == {"launched": [], "errors": []}
    fake_pool.spawn_roster.assert_awaited_once()
