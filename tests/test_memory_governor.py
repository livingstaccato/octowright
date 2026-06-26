# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""OCTOWRIGHT_MIN_FREE_MEMORY_MB launch governor (server/browser/lifecycle)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import defaults as _defaults
from octowright import sysresources as _sysresources
from octowright.browser_pool.errors import MemoryPressureError
from octowright.server.browser import lifecycle as _lifecycle

_MB = 1024 * 1024


def test_min_free_memory_parse_off_values() -> None:
    for off in (None, "", "0", "off", "never", "none", "disabled", "garbage"):
        assert _sysresources.parse_min_free_memory_mb(off) is None


def test_min_free_memory_parse_positive_to_bytes() -> None:
    assert _sysresources.parse_min_free_memory_mb("512") == 512 * _MB
    assert _sysresources.parse_min_free_memory_mb("1.5") == int(1.5 * _MB)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    pool = MagicMock()
    pool.active_count.return_value = 0
    monkeypatch.setattr(_lifecycle, "pool", pool)
    # Cap off so only the memory floor is under test here.
    monkeypatch.setattr(_defaults, "MAX_BROWSERS", None)
    return pool


def _set_floor(monkeypatch: pytest.MonkeyPatch, mb: int | None) -> None:
    monkeypatch.setattr(_sysresources, "MIN_FREE_MEMORY_BYTES", None if mb is None else mb * _MB)


def _set_available(monkeypatch: pytest.MonkeyPatch, mb: int | None) -> None:
    monkeypatch.setattr(_sysresources, "available_memory_bytes", lambda: None if mb is None else mb * _MB)


def test_floor_off_never_reads_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_floor(monkeypatch, None)

    def _boom() -> int:
        raise AssertionError("memory must not be read when the guard is off")

    monkeypatch.setattr(_sysresources, "available_memory_bytes", _boom)
    _lifecycle._enforce_memory_floor(adding=3)  # no raise, no read


def test_floor_above_available_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_floor(monkeypatch, 1024)
    _set_available(monkeypatch, 256)
    with pytest.raises(MemoryPressureError, match="OCTOWRIGHT_MIN_FREE_MEMORY_MB floor"):
        _lifecycle._enforce_memory_floor(adding=1)


def test_floor_below_available_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_floor(monkeypatch, 256)
    _set_available(monkeypatch, 4096)
    _lifecycle._enforce_memory_floor(adding=1)  # plenty free → no raise


def test_unreadable_available_never_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_floor(monkeypatch, 100000)  # huge floor
    _set_available(monkeypatch, None)  # can't read → unknown
    _lifecycle._enforce_memory_floor(adding=1)  # must NOT raise on unknown


@pytest.mark.anyio
async def test_browser_launch_refuses_under_pressure(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    _set_floor(monkeypatch, 2048)
    _set_available(monkeypatch, 512)
    fake_pool.launch = AsyncMock()
    with pytest.raises(MemoryPressureError):
        await _lifecycle.browser_launch(ephemeral=True)
    fake_pool.launch.assert_not_awaited()  # refused before any launch attempt


@pytest.mark.anyio
async def test_spawn_roster_refuses_under_pressure(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    _set_floor(monkeypatch, 2048)
    _set_available(monkeypatch, 512)
    fake_pool.spawn_roster = AsyncMock()
    with pytest.raises(MemoryPressureError):
        await _lifecycle.browser_spawn_roster([{"kind": "chromium"}])
    fake_pool.spawn_roster.assert_not_awaited()


def test_status_memory_block_off_is_two_nulls(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server import meta as _meta

    monkeypatch.setattr(_sysresources, "MIN_FREE_MEMORY_BYTES", None)

    def _boom() -> int:
        raise AssertionError("must not read memory when the floor is off")

    monkeypatch.setattr(_sysresources, "available_memory_bytes", _boom)
    block = _meta._memory_status(_sysresources)
    assert block == {"min_free_memory_mb": None, "available_memory_mb": None}


def test_status_memory_block_reports_floor_and_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server import meta as _meta

    monkeypatch.setattr(_sysresources, "MIN_FREE_MEMORY_BYTES", 1024 * _MB)
    monkeypatch.setattr(_sysresources, "available_memory_bytes", lambda: 4096 * _MB)
    assert _meta._memory_status(_sysresources) == {
        "min_free_memory_mb": 1024,
        "available_memory_mb": 4096,
    }


def test_status_memory_block_handles_unreadable_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.server import meta as _meta

    monkeypatch.setattr(_sysresources, "MIN_FREE_MEMORY_BYTES", 1024 * _MB)
    monkeypatch.setattr(_sysresources, "available_memory_bytes", lambda: None)
    assert _meta._memory_status(_sysresources) == {
        "min_free_memory_mb": 1024,
        "available_memory_mb": None,
    }
