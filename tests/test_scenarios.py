# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

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
    from octowright import personas

    importlib.reload(personas)
    from octowright import scenarios

    importlib.reload(scenarios)
    return scenarios, scen_dir


def _write_yaml(p: Path, doc: dict) -> None:
    p.write_text(yaml.safe_dump(doc))


def test_load_yaml_scenario(fresh_scenarios):
    scenarios, scen_dir = fresh_scenarios
    _write_yaml(
        scen_dir / "raid.yaml",
        {
            "name": "raid",
            "description": "two players plus a monitor",
            "participants": [
                {"persona": "cosmo", "kind": "webkit", "role": "player"},
                {"persona": "ziggy", "kind": "firefox", "role": "player", "startup_macros": ["login"]},
                {"persona": "mortimer", "kind": "chromium", "role": "monitor", "url": "https://ops.octowright.com"},
            ],
            "fixtures": {"mock_routes": [{"pattern": "**/api/time", "status": 200, "body": "{}"}]},
            "teardown": {"macro": "cleanup"},
            "verify": {"player": "assert-in", "monitor": "assert-up"},
        },
    )
    s = scenarios.load_scenario("raid")
    assert s.name == "raid"
    assert len(s.participants) == 3
    assert s.participants[1].persona == "ziggy"
    assert s.participants[1].startup_macros == ["login"]
    assert s.participants[2].url == "https://ops.octowright.com"
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


def test_load_python_scenario(fresh_scenarios, monkeypatch):
    scenarios, scen_dir = fresh_scenarios
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_PY_SCENARIOS", "1")
    (scen_dir / "dyn.py").write_text(
        "from octowright.scenarios import Scenario, Participant\n"
        "def build():\n"
        "    return Scenario(name='dyn', participants=[\n"
        "        Participant(persona='p', kind='webkit', role='player'),\n"
        "    ])\n"
    )
    s = scenarios.load_scenario("dyn")
    assert s.name == "dyn"
    assert len(s.participants) == 1


def test_py_wins_over_yaml(fresh_scenarios, monkeypatch):
    scenarios, scen_dir = fresh_scenarios
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_PY_SCENARIOS", "1")
    (scen_dir / "both.yaml").write_text("name: both\nparticipants: []\n")
    (scen_dir / "both.py").write_text(
        "from octowright.scenarios import Scenario\n"
        "def build():\n"
        "    return Scenario(name='both-py', participants=[])\n"
    )
    s = scenarios.load_scenario("both")
    assert s.name == "both-py"


def test_python_scenario_rejected_by_default(fresh_scenarios, monkeypatch):
    """Default (env var unset) refuses .py scenarios with a clear error
    pointing at the gating env var."""
    scenarios, scen_dir = fresh_scenarios
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_PY_SCENARIOS", raising=False)
    (scen_dir / "dyn.py").write_text(
        "from octowright.scenarios import Scenario\ndef build():\n    return Scenario(name='dyn', participants=[])\n"
    )
    with pytest.raises(RuntimeError, match="OCTOWRIGHT_ALLOW_PY_SCENARIOS"):
        scenarios.load_scenario("dyn")


def test_python_scenario_allowed_when_env_true(fresh_scenarios, monkeypatch):
    """Both ``1`` and ``true`` (case-insensitive) opt in."""
    scenarios, scen_dir = fresh_scenarios
    (scen_dir / "dyn.py").write_text(
        "from octowright.scenarios import Scenario\ndef build():\n    return Scenario(name='dyn', participants=[])\n"
    )
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_PY_SCENARIOS", "true")
    s = scenarios.load_scenario("dyn")
    assert s.name == "dyn"


def test_duplicate_persona_kind_rejected(fresh_scenarios):
    scenarios, scen_dir = fresh_scenarios
    (scen_dir / "dup.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "dup",
                "participants": [
                    {"persona": "a", "kind": "webkit", "role": "player"},
                    {"persona": "a", "kind": "webkit", "role": "monitor"},
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="duplicate"):
        scenarios.load_scenario("dup")


def test_unknown_role_warns(fresh_scenarios, caplog):
    """Typos like ``role: plyer`` would silently fan out to zero targets,
    so loading logs a warning. Custom roles (recorder, main-site, …) are
    legitimate and must not raise."""
    scenarios, scen_dir = fresh_scenarios
    (scen_dir / "bad-role.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "bad-role",
                "participants": [
                    {"persona": "a", "kind": "webkit", "role": "plyer"},
                ],
            }
        )
    )
    with caplog.at_level("WARNING"):
        scenarios.load_scenario("bad-role")
    assert any("unknown_role" in rec.message or "plyer" in rec.message for rec in caplog.records)


def test_resolve_launch_kwargs_defaults(fresh_scenarios, tmp_path):
    scenarios, _ = fresh_scenarios
    from octowright import personas as _p

    # Create a persona with defaults
    pdir = _p.persona_dir("cosmo")
    pdir.mkdir(parents=True)
    (pdir / "profile.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "cosmo",
                "default_url": "https://cosmo-home.example",
                "default_macros": ["login"],
            }
        )
    )
    pov = scenarios.Participant(persona="cosmo", kind="webkit", role="player", url="https://override.example")
    kwargs = scenarios.resolve_launch_kwargs(pov)
    assert kwargs["url"] == "https://override.example"
    assert kwargs["profile"] == "cosmo"
    assert kwargs["kind"] == "webkit"
    assert "role" not in kwargs
    assert "startup_macros" not in kwargs
    assert scenarios.resolve_startup_macros(pov) == ["login"]


def test_resolve_launch_kwargs_no_persona(fresh_scenarios):
    scenarios, _ = fresh_scenarios
    p = scenarios.Participant(persona="ghost", kind="webkit", role="player")
    kwargs = scenarios.resolve_launch_kwargs(p)
    assert kwargs["profile"] == "ghost"
    assert kwargs["url"] is None
    assert scenarios.resolve_startup_macros(p) == []
