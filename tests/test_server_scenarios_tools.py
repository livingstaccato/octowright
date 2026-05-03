# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server import scenarios as _scenarios


@pytest.fixture(autouse=True)
def _patch_deps(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    fake_pool = MagicMock()
    fake_spool = MagicMock()
    monkeypatch.setattr(_scenarios, "pool", fake_pool)
    monkeypatch.setattr(_scenarios, "scenario_pool", fake_spool)
    return {"pool": fake_pool, "scenario_pool": fake_spool}


@pytest.mark.anyio
async def test_scenario_start_returns_shape(_patch_deps: dict[str, MagicMock]) -> None:
    live = MagicMock()
    live.scenario_id = "scen1"
    live.name = "demo"
    live.participants = [{"instance_id": "a"}]
    _patch_deps["scenario_pool"].start = AsyncMock(return_value=live)
    out = await _scenarios.scenario_start("demo")
    assert out["scenario_id"] == "scen1"
    assert out["participants"][0]["instance_id"] == "a"


def test_scenario_remap_participants_forwards(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["scenario_pool"].remap_participants.return_value = {"scenario_id": "s", "count": 1, "applied": []}
    out = _scenarios.scenario_remap_participants("s", [{"old_instance_id": "a", "new_instance_id": "b"}])
    assert out["count"] == 1
    _patch_deps["scenario_pool"].remap_participants.assert_called_once()
