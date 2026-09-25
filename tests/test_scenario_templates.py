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


_TEMPLATE = 'name: t\nparticipants:\n  - persona: "{{p}}"\n    kind: chromium\n    role: player\n'


@pytest.mark.parametrize("sep", ["\x85", "\u2028", "\u2029"])
def test_load_scenario_template_rejects_every_yaml_line_break(fresh_scenarios, sep):
    """PyYAML breaks lines on NEL, LINE SEPARATOR and PARAGRAPH SEPARATOR too.

    Verified: with only \\n/\\r refused, ``cosmo"<sep>    url: "http://evil``
    added a ``url`` key to the participant.
    """
    scenarios, template_dir = fresh_scenarios
    (template_dir / "sep.yaml").write_text(_TEMPLATE, encoding="utf-8")
    with pytest.raises(ValueError, match="newline"):
        scenarios.load_scenario_template("sep", {"p": f'cosmo"{sep}    url: "http://evil.test/'})


def test_load_scenario_template_value_cannot_break_out_of_its_quotes(fresh_scenarios):
    """No line break needed in a flow mapping: a quote ends the scalar.

    Raw text substitution let ``cosmo", url: "http://evil.test/", x: "`` add a
    ``url`` key; substituting into the parsed structure keeps it one string.
    """
    scenarios, template_dir = fresh_scenarios
    (template_dir / "flow.yaml").write_text(
        'name: t\nparticipants: [{persona: "{{p}}", kind: chromium, role: player}]\n', encoding="utf-8"
    )
    value = 'cosmo", url: "http://evil.test/", x: "'
    scenario = scenarios.load_scenario_template("flow", {"p": value})
    assert scenario.participants[0].persona == value
    assert scenario.participants[0].url is None


def test_load_scenario_template_value_cannot_become_structure(fresh_scenarios):
    """An unquoted-looking value stays a string, not a YAML list or mapping."""
    scenarios, template_dir = fresh_scenarios
    (template_dir / "shape.yaml").write_text(_TEMPLATE, encoding="utf-8")
    scenario = scenarios.load_scenario_template("shape", {"p": "[a, b]"})
    assert scenario.participants[0].persona == "[a, b]"


def test_load_scenario_template_with_an_unquoted_placeholder_says_so(fresh_scenarios):
    scenarios, template_dir = fresh_scenarios
    (template_dir / "bare.yaml").write_text(
        "name: t\nparticipants:\n  - persona: {{p}}\n    kind: chromium\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="quote"):
        scenarios.load_scenario_template("bare", {"p": "cosmo"})
