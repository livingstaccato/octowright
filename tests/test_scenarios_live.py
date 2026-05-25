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


# Asyncio-only: BrowserPool is built on Playwright's asyncio API and is not trio-compatible.
@pytest.mark.asyncio
async def test_scenario_start_and_stop_live(tmp_octowright, monkeypatch):
    root = tmp_octowright
    (root / "scn").mkdir(exist_ok=True)
    (root / "prof").mkdir(exist_ok=True)

    # Two personas with no default_url so the scenario's explicit url is used.
    for name in ("p1", "p2"):
        pdir = root / "prof" / name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "profile.yaml").write_text(yaml.safe_dump({"name": name}))

    # about:blank avoids any external network — important for CI / sandboxed runners
    # where outbound HTTP may be blocked or slow.
    (root / "scn" / "mini.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "mini",
                "participants": [
                    {"persona": "p1", "kind": "webkit", "role": "player", "url": "about:blank"},
                    {"persona": "p2", "kind": "webkit", "role": "monitor", "url": "about:blank"},
                ],
                "fixtures": {"dialog_policy": "dismiss"},
            }
        )
    )

    # Force headless globally via env — reload defaults and pool so HEADLESS_DEFAULT
    # is picked up by scenario-driven launches too.
    monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "1")
    for m in ("octowright.defaults", "octowright.browser_pool.pool"):
        if m in sys.modules:
            importlib.reload(sys.modules[m])

    from octowright import scenarios as _s
    from octowright import scenarios_pool as _sp
    from octowright.browser_pool import BrowserPool

    # Reload scenarios so it picks up the freshly-reloaded defaults.
    importlib.reload(_s)
    importlib.reload(_sp)

    pool = BrowserPool()
    spool = _sp.ScenarioPool()
    try:
        live = await spool.start(name="mini", browser_pool=pool)
        assert len(live.participants) == 2
        roles = [p["role"] for p in live.participants]
        assert set(roles) == {"player", "monitor"}
        # scenario_status reports the live scenario
        status = spool.list_live()
        assert len(status) == 1
        assert status[0]["name"] == "mini"
        # Stop cleanly
        summary = await spool.stop(scenario_id=live.scenario_id, browser_pool=pool)
        assert len(summary["closed"]) == 2
        assert summary["teardown_errors"] == []
        assert spool.list_live() == []
    finally:
        await pool.shutdown()
