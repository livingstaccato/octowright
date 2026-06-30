# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
import sys

import pytest
import yaml


@pytest.fixture
def tmp_octowright(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "rec"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "prof"))
    monkeypatch.setenv("OCTOWRIGHT_SCENARIOS_DIR", str(tmp_path / "scn"))
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "mac"))
    for m in (
        "octowright.defaults",
        "octowright.personas",
        "octowright.engine_profiles",
        "octowright.scenarios",
        "octowright.macros.storage",
        "octowright.macros",
    ):
        if m in sys.modules:
            importlib.reload(sys.modules[m])
    yield tmp_path


@pytest.mark.asyncio
async def test_scenario_wait_for_sync(tmp_octowright, monkeypatch):
    root = tmp_octowright
    (root / "scn").mkdir(exist_ok=True)
    (root / "prof").mkdir(exist_ok=True)

    for name in ("p1", "p2"):
        pdir = root / "prof" / name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "profile.yaml").write_text(yaml.safe_dump({"name": name}))

    (root / "scn" / "sync.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "sync",
                "participants": [
                    {
                        "persona": "p1",
                        "kind": "chromium",
                        "role": "player",
                        "url": "data:text/html,<html><div id='p'>player</div></html>",
                    },
                    {
                        "persona": "p2",
                        "kind": "chromium",
                        "role": "monitor",
                        "url": "data:text/html,<html><div id='m'>monitor</div></html>",
                    },
                ],
            }
        )
    )

    monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "1")
    for m in ("octowright.defaults", "octowright.browser_pool.pool"):
        if m in sys.modules:
            importlib.reload(sys.modules[m])

    from octowright import scenarios as _s
    from octowright import scenarios_pool as _sp
    from octowright.browser_pool import BrowserPool

    importlib.reload(_s)
    importlib.reload(_sp)

    _original_spawn = BrowserPool.spawn_roster

    async def _headless_spawn(self, specs):
        patched = [{**spec, "headed": False} for spec in specs]
        return await _original_spawn(self, patched)

    monkeypatch.setattr(BrowserPool, "spawn_roster", _headless_spawn)

    pool = BrowserPool()
    spool = _sp.ScenarioPool()
    try:
        live = await spool.start(name="sync", browser_pool=pool)

        # 1. Test URL sync (regex)
        res = await spool.wait_for_sync(
            scenario_id=live.scenario_id, browser_pool=pool, url=".*text/html.*", role="player"
        )
        assert res["targeted"] == 1
        if not res["results"][0]["ok"]:
            print(f"URL sync error: {res['results'][0]['error']}")
        assert res["results"][0]["ok"] is True

        # 2. Test Selector sync
        res = await spool.wait_for_sync(scenario_id=live.scenario_id, browser_pool=pool, selector="#p", role="player")
        assert res["targeted"] == 1
        assert res["results"][0]["ok"] is True

        # 3. Test Text sync
        res = await spool.wait_for_sync(scenario_id=live.scenario_id, browser_pool=pool, text="monitor", role="monitor")
        assert res["targeted"] == 1
        assert res["results"][0]["ok"] is True

        # 4. Test timeout (failure)
        res = await spool.wait_for_sync(
            scenario_id=live.scenario_id, browser_pool=pool, selector="#nonexistent", timeout_ms=100
        )
        assert all(not r["ok"] for r in res["results"])
        assert "Timeout" in res["results"][0]["error"]

    finally:
        await pool.shutdown()
