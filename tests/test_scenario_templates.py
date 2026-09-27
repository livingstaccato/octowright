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


# --- A placeholder that is the whole scalar takes the value's YAML type ----
#
# The template is parsed before substitution, so ``headed: "{{headed}}"``
# used to become the STRING "false" -- truthy, and rejected by the bool
# validators. A quoted scalar that is exactly one placeholder now resolves
# the substituted text as a YAML 1.2 core-schema scalar (bool/int/float/null)
# and nothing else: no mapping, sequence, tag or anchor can come from an arg.

_TYPED = (
    "name: t\n"
    "participants:\n"
    '  - persona: "{{p}}"\n'
    "    kind: chromium\n"
    '    record_video: "{{video}}"\n'
    '    viewport_w: "{{w}}"\n'
    '    url: "{{url}}"\n'
    "verify:\n"
    '  ratio: "{{ratio}}"\n'
    '  label: "run-{{video}}"\n'
)


def _typed(fresh_scenarios, **args):
    scenarios, template_dir = fresh_scenarios
    (template_dir / "typed.yaml").write_text(_TYPED, encoding="utf-8")
    base = {"p": "cosmo", "video": "false", "w": "1280", "url": "null", "ratio": "0.5"}
    return scenarios.load_scenario_template("typed", {**base, **args})


def test_whole_placeholder_takes_bool_int_float_and_null(fresh_scenarios):
    scenario = _typed(fresh_scenarios)
    participant = scenario.participants[0]
    assert participant.record_video is False
    assert participant.viewport_w == 1280
    assert participant.url is None
    assert scenario.verify["ratio"] == 0.5


@pytest.mark.parametrize(
    ("text", "expected"),
    [("true", True), ("True", True), ("TRUE", True), ("false", False), ("~", None), ("Null", None)],
)
def test_whole_placeholder_follows_the_yaml_core_spellings(fresh_scenarios, text, expected):
    scenario = _typed(fresh_scenarios, url=text)
    assert scenario.participants[0].url is expected


def test_native_json_args_keep_their_type(fresh_scenarios):
    """An MCP caller passing a real bool/int is not round-tripped through str."""
    participant = _typed(fresh_scenarios, video=True, w=800).participants[0]
    assert participant.record_video is True
    assert participant.viewport_w == 800


def test_embedded_placeholder_stays_a_string(fresh_scenarios):
    assert _typed(fresh_scenarios).verify["label"] == "run-false"


@pytest.mark.parametrize(
    "text",
    [
        "yes",  # a YAML 1.1 bool, not a 1.2 one: the Norway problem stays out
        "on",
        "[a, b]",
        "{a: 1}",
        "!!python/object:os.system x",
        "&anchor x",
        "*alias",
        "2026-09-27",  # a YAML timestamp is not in the coerced set
        "",
        "0x1F-ish",
    ],
)
def test_whole_placeholder_never_becomes_structure_or_an_unlisted_type(fresh_scenarios, text):
    assert _typed(fresh_scenarios, url=text).participants[0].url == text


def test_bundled_collaboration_template_still_loads(fresh_scenarios):
    from pathlib import Path

    scenarios, template_dir = fresh_scenarios
    bundled = Path(__file__).resolve().parents[1] / "examples" / "scenarios" / "templates" / "collaboration.yaml"
    (template_dir / "collaboration.yaml").write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
    scenario = scenarios.load_scenario_template("collaboration", {"persona_1": "cosmo", "persona_2": "ziggy"})
    assert [p.persona for p in scenario.participants] == ["cosmo", "ziggy"]


def test_unquoted_placeholder_error_names_the_line_and_the_fix(fresh_scenarios):
    scenarios, template_dir = fresh_scenarios
    (template_dir / "bare2.yaml").write_text(
        "name: t\nparticipants:\n  - persona: cosmo\n    kind: chromium\n    record_video: {{video}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as info:
        scenarios.load_scenario_template("bare2", {"video": "true"})
    message = str(info.value)
    assert "line 5" in message
    assert 'record_video: "{{video}}"' in message
    # Quoting does not cost the type, and the message says so.
    assert "boolean" in message
