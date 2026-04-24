from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def fresh_scenarios(tmp_path, monkeypatch):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    monkeypatch.setenv("OCTOWRIGHT_SCENARIOS_DIR", str(scen_dir))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    from octowright import defaults
    importlib.reload(defaults)
    from octowright import scenarios
    importlib.reload(scenarios)
    return scenarios, scen_dir


def _write_yaml(p: Path, doc: dict) -> None:
    p.write_text(yaml.safe_dump(doc))


def test_load_yaml_scenario(fresh_scenarios):
    scenarios, scen_dir = fresh_scenarios
    _write_yaml(scen_dir / "raid.yaml", {
        "name": "raid",
        "description": "two players plus a monitor",
        "participants": [
            {"persona": "alice", "kind": "webkit", "role": "player"},
            {"persona": "bob",   "kind": "firefox", "role": "player",
             "startup_macros": ["login"]},
            {"persona": "ops",   "kind": "chromium", "role": "monitor",
             "url": "https://ops.example.com"},
        ],
        "fixtures": {"mock_routes": [{"pattern": "**/api/time", "status": 200, "body": "{}"}]},
        "teardown": {"macro": "cleanup"},
        "verify": {"player": "assert-in", "monitor": "assert-up"},
    })
    s = scenarios.load_scenario("raid")
    assert s.name == "raid"
    assert len(s.participants) == 3
    assert s.participants[1].persona == "bob"
    assert s.participants[1].startup_macros == ["login"]
    assert s.participants[2].url == "https://ops.example.com"
    assert s.fixtures["mock_routes"][0]["pattern"] == "**/api/time"
    assert s.teardown_macro == "cleanup"
    assert s.verify == {"player": "assert-in", "monitor": "assert-up"}


def test_missing_scenario_raises(fresh_scenarios):
    scenarios, _ = fresh_scenarios
    with pytest.raises(FileNotFoundError):
        scenarios.load_scenario("ghost")


def test_list_scenarios_sorted(fresh_scenarios):
    scenarios, scen_dir = fresh_scenarios
    _write_yaml(scen_dir / "a.yaml", {"name": "a", "participants": []})
    _write_yaml(scen_dir / "b.yaml", {"name": "b", "participants": []})
    rows = scenarios.list_scenarios()
    names = sorted(r["name"] for r in rows)
    assert names == ["a", "b"]
