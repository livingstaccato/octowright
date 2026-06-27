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


def test_cap_defaults_on_at_32() -> None:
    # The cap now defaults ON so peak memory pressure can't drive cascading
    # renderer crashes; unset env resolves to MAX_BROWSERS_DEFAULT.
    assert _defaults.MAX_BROWSERS_DEFAULT == "32"
    assert _defaults._parse_max_browsers(None) is None  # truly-None arg = no env passed
    assert _defaults._parse_max_browsers(_defaults.MAX_BROWSERS_DEFAULT) == 32


def test_cap_env_off_disables() -> None:
    for off in ("off", "0", "never", "none", "disabled", ""):
        assert _defaults._parse_max_browsers(off) is None


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


def test_cap_refusal_is_metered(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    from octowright.browser_pool import limits as _limits
    from tests._metric_recorders import RecordingCounter

    refused = RecordingCounter()
    # The counter now lives in the pool-layer limits module (the gate moved off
    # the tool wrapper so the scenario path can't bypass it).
    monkeypatch.setattr(_limits, "LAUNCH_REFUSED", refused)
    _set_cap(monkeypatch, 2)
    fake_pool.active_count.return_value = 2
    with pytest.raises(BrowserCapExceededError):
        _lifecycle._enforce_browser_cap(adding=1)
    assert refused.total() == 1
    assert refused.attrs_for("reason") == ["cap"]


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


@pytest.mark.anyio
async def test_roster_chokepoint_enforces_for_scenario_bypass(
    monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock
) -> None:
    """scenario_start calls ``pool.spawn_roster`` directly (bypassing the tool
    wrapper's pre-check), so the cap must also live at the roster chokepoint —
    otherwise a big scenario OOMs the shared host. Driving roster.spawn_roster
    directly proves the gate is at the layer scenario_start actually hits."""
    from octowright.browser_pool import roster

    _set_cap(monkeypatch, 4)
    fake_pool.active_count.return_value = 3
    fake_pool.launch = AsyncMock()
    with pytest.raises(BrowserCapExceededError):
        await roster.spawn_roster(fake_pool, [{"kind": "chromium"}, {"kind": "firefox"}])
    # All-or-nothing: not a single browser was launched.
    fake_pool.launch.assert_not_awaited()


@pytest.mark.anyio
async def test_roster_chokepoint_allows_within_cap(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    from octowright.browser_pool import roster

    _set_cap(monkeypatch, 8)
    fake_pool.active_count.return_value = 1
    fake_pool.launch = AsyncMock(return_value={"instance_id": "x"})
    out = await roster.spawn_roster(fake_pool, [{"kind": "chromium"}, {"kind": "firefox"}])
    assert len(out["launched"]) == 2
    assert fake_pool.launch.await_count == 2
