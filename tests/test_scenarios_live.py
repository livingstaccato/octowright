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
        "octowright.profiles",
        "octowright.scenarios",
        "octowright.macros",
    ):
        if m in sys.modules:
            importlib.reload(sys.modules[m])
    yield tmp_path


@pytest.mark.anyio
async def test_scenario_start_and_stop_live(tmp_octowright, monkeypatch):
    root = tmp_octowright
    (root / "scn").mkdir(exist_ok=True)
    (root / "prof").mkdir(exist_ok=True)

    # Two personas with no default_url so the scenario's explicit url is used.
    for name in ("p1", "p2"):
        pdir = root / "prof" / name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "profile.yaml").write_text(yaml.safe_dump({"name": name}))

    (root / "scn" / "mini.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "mini",
                "participants": [
                    {"persona": "p1", "kind": "webkit", "role": "player", "url": "https://example.com"},
                    {"persona": "p2", "kind": "webkit", "role": "monitor", "url": "https://example.com"},
                ],
                "fixtures": {"dialog_policy": "dismiss"},
            }
        )
    )

    # Force headless globally via env — reload defaults and pool so HEADLESS_DEFAULT
    # is picked up. Also patch spawn_roster to inject headed=False into every spec
    # since spawn_roster defaults headed=True (designed for interactive use).
    monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "1")
    for m in ("octowright.defaults", "octowright.pool"):
        if m in sys.modules:
            importlib.reload(sys.modules[m])

    from octowright import scenarios as _s
    from octowright.pool import BrowserPool

    # Reload scenarios so it picks up the freshly-reloaded defaults.
    importlib.reload(_s)

    # Patch spawn_roster to inject headed=False so HEADLESS_DEFAULT is honoured
    # (spawn_roster normally defaults headed=True for interactive use).
    _original_spawn = BrowserPool.spawn_roster

    async def _headless_spawn(self, specs):  # type: ignore[override]
        patched = [{**spec, "headed": False} for spec in specs]
        return await _original_spawn(self, patched)

    monkeypatch.setattr(BrowserPool, "spawn_roster", _headless_spawn)

    pool = BrowserPool()
    spool = _s.ScenarioPool()
    try:
        live = await spool.start(name="mini", browser_pool=pool)
        assert len(live.participants) == 2
        roles = [p["role"] for p in live.participants]
        assert set(roles) == {"player", "monitor"}
        # scenario_status reports the live scenario
        status = spool.list()
        assert len(status) == 1
        assert status[0]["name"] == "mini"
        # Stop cleanly
        summary = await spool.stop(scenario_id=live.scenario_id, browser_pool=pool)
        assert len(summary["closed"]) == 2
        assert summary["teardown_errors"] == []
        assert spool.list() == []
    finally:
        await pool.shutdown()
