# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib

import pytest
import yaml


@pytest.fixture
def fresh_scenarios(tmp_path, monkeypatch):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    template_dir = scen_dir / "templates"
    template_dir.mkdir()

    monkeypatch.setenv("OCTOWRIGHT_SCENARIOS_DIR", str(scen_dir))

    from octowright import defaults

    importlib.reload(defaults)

    from octowright import scenarios

    importlib.reload(scenarios)

    return scenarios, template_dir


def test_load_scenario_template(fresh_scenarios):
    scenarios, template_dir = fresh_scenarios

    template_path = template_dir / "collaboration.yaml"
    template_path.write_text(
        yaml.safe_dump(
            {
                "name": "Collaboration Template",
                "participants": [
                    {"persona": "{{persona_1}}", "kind": "chromium", "role": "player"},
                    {"persona": "{{persona_2}}", "kind": "firefox", "role": "spectator"},
                ],
            }
        )
    )

    s = scenarios.load_scenario_template("collaboration", {"persona_1": "cosmo", "persona_2": "ziggy"})

    assert s.name == "Collaboration Template"
    assert len(s.participants) == 2
    assert s.participants[0].persona == "cosmo"
    assert s.participants[0].kind == "chromium"
    assert s.participants[0].role == "player"
    assert s.participants[1].persona == "ziggy"
    assert s.participants[1].kind == "firefox"
    assert s.participants[1].role == "spectator"


def test_load_missing_template_raises(fresh_scenarios):
    scenarios, _ = fresh_scenarios
    with pytest.raises(FileNotFoundError, match="no scenario template named 'ghost'"):
        scenarios.load_scenario_template("ghost", {})


def test_load_scenario_template_rejects_parent_traversal(fresh_scenarios):
    """Template name must not escape SCENARIO_TEMPLATES_DIR."""
    scenarios, _ = fresh_scenarios
    with pytest.raises(ValueError, match="resolves outside"):
        scenarios.load_scenario_template("../../etc/passwd", {})


def test_load_scenario_template_rejects_arg_with_newline(fresh_scenarios):
    """Newlines in template-arg values would inject YAML structure post-substitution."""
    scenarios, template_dir = fresh_scenarios
    template_path = template_dir / "inject.yaml"
    template_path.write_text(
        yaml.safe_dump(
            {
                "name": "Inject",
                "participants": [{"persona": "{{p}}", "kind": "chromium", "role": "player"}],
            }
        )
    )
    with pytest.raises(ValueError, match="newline"):
        scenarios.load_scenario_template("inject", {"p": "cosmo\n  - evil_extra"})


def test_load_scenario_template_rejects_arg_with_carriage_return(fresh_scenarios):
    """CR alone is also rejected — Windows-style line endings carry the same risk."""
    scenarios, template_dir = fresh_scenarios
    template_path = template_dir / "inject_cr.yaml"
    template_path.write_text(
        yaml.safe_dump(
            {
                "name": "Inject",
                "participants": [{"persona": "{{p}}", "kind": "chromium", "role": "player"}],
            }
        )
    )
    with pytest.raises(ValueError, match="newline"):
        scenarios.load_scenario_template("inject_cr", {"p": "cosmo\r evil"})


def test_load_scenario_rejects_parent_traversal(fresh_scenarios):
    """``load_scenario`` is also reachable from MCP via scenario_start; same guard applies."""
    scenarios, _ = fresh_scenarios
    with pytest.raises(ValueError, match="resolves outside"):
        scenarios.load_scenario("../../etc/passwd")
