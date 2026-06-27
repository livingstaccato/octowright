# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Driver-death self-heal: a dead shared Playwright driver used to brick the
whole pool until restart. pool.launch now resets the driver and retries once."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import driver_health
from octowright.browser_pool.pool import BrowserPool


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_is_driver_dead_error_matches_connection_closed() -> None:
    assert driver_health.is_driver_dead_error(RuntimeError("Browser.new_context: Connection closed")) is True
    assert driver_health.is_driver_dead_error(ValueError("Target page, context or browser has been closed")) is True
    assert driver_health.is_driver_dead_error(ValueError("I/O operation on closed file")) is True


def test_is_driver_dead_error_ignores_ordinary_failures() -> None:
    assert driver_health.is_driver_dead_error(RuntimeError("net::ERR_NAME_NOT_RESOLVED")) is False
    assert driver_health.is_driver_dead_error(ValueError("Executable doesn't exist at .../pw_run.sh")) is False


@pytest.mark.anyio
async def test_reset_driver_stops_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    dead = MagicMock()
    dead.stop = AsyncMock()
    pool._pw = dead
    await pool._reset_driver()
    assert pool._pw is None
    dead.stop.assert_awaited_once()


@pytest.mark.anyio
async def test_reset_driver_swallows_stop_failure() -> None:
    pool = BrowserPool()
    dead = MagicMock()
    dead.stop = AsyncMock(side_effect=RuntimeError("already gone"))
    pool._pw = dead
    await pool._reset_driver()  # must not raise
    assert pool._pw is None


@pytest.mark.anyio
async def test_launch_retries_once_after_driver_death(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    reset = AsyncMock()
    monkeypatch.setattr(pool, "_reset_driver", reset)

    calls = {"n": 0}

    async def _impl(_options: dict, _sp: object) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("BrowserType.launch: Connection closed")
        return {"instance_id": "healed"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)
    out = await pool.launch(kind="chromium")
    assert out == {"instance_id": "healed"}
    assert calls["n"] == 2  # failed once, retried once
    reset.assert_awaited_once()  # driver was rebuilt between attempts


@pytest.mark.anyio
async def test_launch_does_not_retry_ordinary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    reset = AsyncMock()
    monkeypatch.setattr(pool, "_reset_driver", reset)

    async def _impl(_options: dict, _sp: object) -> dict:
        raise RuntimeError("net::ERR_CONNECTION_REFUSED")

    monkeypatch.setattr(pool, "_launch_impl", _impl)
    with pytest.raises(RuntimeError, match="ERR_CONNECTION_REFUSED"):
        await pool.launch(kind="chromium")
    reset.assert_not_awaited()  # ordinary launch failures don't reset the driver


@pytest.mark.anyio
async def test_launch_reraises_if_retry_also_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = BrowserPool()
    monkeypatch.setattr(pool, "_reset_driver", AsyncMock())

    async def _impl(_options: dict, _sp: object) -> dict:
        raise RuntimeError("Connection closed")

    monkeypatch.setattr(pool, "_launch_impl", _impl)
    # No infinite loop: one retry, then the second failure propagates.
    with pytest.raises(RuntimeError, match="Connection closed"):
        await pool.launch(kind="chromium")


def test_driver_restart_count_surfaced() -> None:
    pool = BrowserPool()
    assert pool.driver_restart_count() == 0
