# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Scenario loading, validation and launch resolution, pinned record by record.

Scenario, template and profile directories are temporary; the Python-scenario
opt-in is set explicitly per test.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from octowright import personas, scenarios
from octowright.scenario_kinds import known_kinds

PY_ENV = "OCTOWRIGHT_ALLOW_PY_SCENARIOS"


class LogRecorder:
    """Stands in for the module logger: keeps every warning as ``(event, fields)``."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **fields: Any) -> None:
        self.warnings.append((event, fields))

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    found = {
        "scenarios": tmp_path / "scenarios",
        "templates": tmp_path / "templates",
        "profiles": tmp_path / "profiles",
    }
    monkeypatch.setattr(scenarios, "SCENARIOS_DIR", found["scenarios"])
    monkeypatch.setattr(scenarios, "SCENARIO_TEMPLATES_DIR", found["templates"])
    monkeypatch.setattr(personas, "PROFILES_DIR", found["profiles"])
    monkeypatch.delenv(PY_ENV, raising=False)
    return found


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> LogRecorder:
    recorder = LogRecorder()
    monkeypatch.setattr(scenarios, "log", recorder)
    return recorder


def _error(exc_type: type[BaseException], func: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(exc_type) as raised:
        func(*args, **kwargs)
    return str(raised.value)


def _load(doc: Any, name: str = "s") -> scenarios.Scenario:
    return scenarios.load_yaml_scenario(yaml.safe_dump(doc), name)


def _one(participant: dict[str, Any], name: str = "s") -> str:
    return _error(ValueError, _load, {"participants": [participant]}, name)


P = {"persona": "ann", "kind": "chromium"}


# --- whole-scenario loading -----------------------------------------------------------


def test_a_yaml_scenario_loads_every_field(logged: LogRecorder) -> None:
    scenario = _load(
        {
            "name": "checkout",
            "description": "two buyers",
            "participants": [
                {
                    "persona": "ann",
                    "kind": "chromium",
                    "role": "monitor",
                    "url": "https://a.test",
                    "startup_macros": ["login"],
                    "viewport_w": 800,
                    "viewport_h": 600,
                    "stabilize": True,
                    "record_video": False,
                    "trace": True,
                    "options": {"k": "v"},
                },
                {"persona": "bob", "kind": "firefox"},
            ],
            "fixtures": {
                "dialog_policy": "dismiss",
                "mock_routes": [
                    {
                        "pattern": "**/api",
                        "status": 201,
                        "body": None,
                        "content_type": "text/plain",
                        "headers": {"a": "b"},
                    },
                    {"pattern": "**/x"},
                ],
            },
            "teardown": {"macro": "logout"},
            "verify": {"ann": "check"},
        },
        name="file-name",
    )
    assert scenario == scenarios.Scenario(
        name="checkout",
        participants=[
            scenarios.Participant(
                persona="ann",
                kind="chromium",
                role="monitor",
                url="https://a.test",
                startup_macros=["login"],
                viewport_w=800,
                viewport_h=600,
                stabilize=True,
                record_video=False,
                trace=True,
                options={"k": "v"},
            ),
            scenarios.Participant(persona="bob", kind="firefox", role="player"),
        ],
        description="two buyers",
        fixtures={
            "dialog_policy": "dismiss",
            "mock_routes": [
                {"pattern": "**/api", "status": 201, "body": None, "content_type": "text/plain", "headers": {"a": "b"}},
                {"pattern": "**/x"},
            ],
        },
        teardown_macro="logout",
        verify={"ann": "check"},
    )
    assert logged.warnings == []


def test_a_minimal_scenario_takes_its_name_from_the_file() -> None:
    assert _load({"participants": [P]}, name="from-file") == scenarios.Scenario(
        name="from-file", participants=[scenarios.Participant(persona="ann", kind="chromium", role="player")]
    )


@pytest.mark.parametrize("teardown", ["logout", ["logout"], None, {}])
def test_a_teardown_that_is_not_a_mapping_with_a_macro_has_none(teardown: Any) -> None:
    assert _load({"participants": [P], "teardown": teardown}).teardown_macro is None


@pytest.mark.parametrize(("raw", "got"), [("- a\n- b\n", "list"), ("just text\n", "str"), ("", "NoneType")])
def test_a_scenario_that_is_not_a_mapping_loads_empty_and_warns(raw: str, got: str, logged: LogRecorder) -> None:
    assert scenarios.load_yaml_scenario(raw, "odd") == scenarios.Scenario(name="odd", participants=[])
    assert logged.warnings == [("scenarios.yaml_not_mapping", {"name": "odd", "got": got})]


# --- participant validation -----------------------------------------------------------


def test_a_participant_that_is_not_a_mapping_is_refused() -> None:
    assert _error(ValueError, _load, {"participants": [P, "ann"]}) == (
        "scenario 's': participants[1] must be a mapping, got str"
    )


@pytest.mark.parametrize(
    ("participant", "field_name"),
    [
        ({"kind": "chromium"}, "persona"),
        ({"persona": "", "kind": "chromium"}, "persona"),
        ({"persona": "a"}, "kind"),
        ({"persona": "a", "kind": 3}, "kind"),
    ],
)
def test_a_participant_without_persona_or_kind_is_refused(participant: dict[str, Any], field_name: str) -> None:
    assert _one(participant) == f"scenario 's': participants[0] missing required {field_name!r}"


def test_an_unknown_kind_names_every_known_kind() -> None:
    assert _one({"persona": "a", "kind": "netscape"}) == (
        f"scenario 's': participant has unsupported kind 'netscape' (known kinds: {known_kinds()}) "
        "-- a plugin kind must be enabled via OCTOWRIGHT_PLUGINS"
    )


def test_a_duplicate_persona_and_kind_is_refused_but_another_kind_is_not() -> None:
    _load({"participants": [P, {"persona": "ann", "kind": "webkit"}]})
    assert _error(ValueError, _load, {"participants": [P, {**P, "role": "monitor"}]}) == (
        "scenario 's': duplicate (persona, kind) pair ('ann', 'chromium')"
    )


def test_an_unknown_role_warns_with_the_known_roles(logged: LogRecorder) -> None:
    _load(
        {"name": "n", "participants": [{**P, "role": "plyer"}, {"persona": "b", "kind": "webkit", "role": "spectator"}]}
    )
    assert logged.warnings == [
        (
            "scenario.unknown_role",
            {"scenario": "n", "persona": "ann", "role": "plyer", "known": ["monitor", "player", "spectator"]},
        )
    ]


@pytest.mark.parametrize("macros", ["login", ["login", 2], {"a": 1}])
def test_startup_macros_must_be_a_list_of_strings(macros: Any) -> None:
    assert _one({**P, "startup_macros": macros}) == (
        "scenario 's': participants[0] 'startup_macros' must be a list of strings"
    )


def test_options_must_be_a_mapping_and_are_copied() -> None:
    assert _one({**P, "options": ["x"]}) == "scenario participant 'ann': options must be a mapping"
    options = {"k": 1}
    scenario = scenarios._scenario_from_raw({"participants": [{**P, "options": options}]}, "s")
    assert scenario.participants[0].options == {"k": 1}
    assert scenario.participants[0].options is not options


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("1280", "got str '1280' (unquote it in the YAML, or pass a JSON number as the template arg)"),
        ("wide", "got str 'wide'"),
        (True, "got bool True"),
        (1.5, "got float 1.5"),
    ],
)
def test_a_viewport_that_is_not_an_integer_is_refused(value: Any, shown: str) -> None:
    assert _one({**P, "viewport_h": value}) == f"scenario 's': participants[0] 'viewport_h' must be an integer, {shown}"


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("True", ", got 'True' (only lowercase true/false are read as a boolean: write true, or pass a JSON bool)"),
        ("yes", ""),
        (1, ""),
    ],
)
@pytest.mark.parametrize("field_name", ["stabilize", "record_video", "trace"])
def test_a_flag_that_is_not_a_boolean_is_refused(field_name: str, value: Any, shown: str) -> None:
    assert _one({**P, field_name: value}) == f"scenario 's': participants[0] {field_name!r} must be a boolean{shown}"


# --- fixtures -------------------------------------------------------------------------

FIXTURE_CASES = [
    ("x", "fixtures must be a mapping"),
    ({"zeta": 1, "alpha": 2, "dialog_policy": "accept"}, "fixtures contain unknown keys: ['alpha', 'zeta']"),
    ({"dialog_policy": "ok"}, "fixtures.dialog_policy must be one of accept, dismiss, manual"),
    ({"mock_routes": {"pattern": "x"}}, "fixtures.mock_routes must be a list"),
    ({"mock_routes": [{"pattern": "a"}, "b"]}, "fixtures.mock_routes[1] must be a mapping"),
    ({"mock_routes": [{"status": 200}]}, "fixtures.mock_routes[0].pattern must be a non-empty string"),
    ({"mock_routes": [{"pattern": ""}]}, "fixtures.mock_routes[0].pattern must be a non-empty string"),
    (
        {"mock_routes": [{"pattern": "a", "status": True}]},
        "fixtures.mock_routes[0].status must be an integer from 100 to 599",
    ),
    (
        {"mock_routes": [{"pattern": "a", "status": 99}]},
        "fixtures.mock_routes[0].status must be an integer from 100 to 599",
    ),
    (
        {"mock_routes": [{"pattern": "a", "status": 600}]},
        "fixtures.mock_routes[0].status must be an integer from 100 to 599",
    ),
    (
        {"mock_routes": [{"pattern": "a", "status": "200"}]},
        "fixtures.mock_routes[0].status must be an integer from 100 to 599",
    ),
    ({"mock_routes": [{"pattern": "a", "body": 1}]}, "fixtures.mock_routes[0].body must be a string or null"),
    (
        {"mock_routes": [{"pattern": "a", "content_type": ""}]},
        "fixtures.mock_routes[0].content_type must be a non-empty string",
    ),
    (
        {"mock_routes": [{"pattern": "a", "content_type": 1}]},
        "fixtures.mock_routes[0].content_type must be a non-empty string",
    ),
    (
        {"mock_routes": [{"pattern": "a", "headers": ["x"]}]},
        "fixtures.mock_routes[0].headers must be a mapping of strings",
    ),
    (
        {"mock_routes": [{"pattern": "a", "headers": {"a": 1}}]},
        "fixtures.mock_routes[0].headers must be a mapping of strings",
    ),
    (
        {"mock_routes": [{"pattern": "a", "headers": {1: "a"}}]},
        "fixtures.mock_routes[0].headers must be a mapping of strings",
    ),
    (
        {"mock_routes": [{"pattern": "a", "zeta": 1, "alpha": 1}]},
        "fixtures.mock_routes[0] unknown keys: ['alpha', 'zeta']",
    ),
]


@pytest.mark.parametrize(("fixtures", "reason"), FIXTURE_CASES)
def test_an_invalid_fixture_is_refused_with_its_exact_reason(fixtures: Any, reason: str) -> None:
    assert _error(ValueError, _load, {"participants": [P], "fixtures": fixtures}) == f"scenario 's': {reason}"


def test_fixture_routes_keep_only_the_fields_given_and_status_bounds_are_inclusive() -> None:
    routes = [
        {"pattern": "a", "status": 100},
        {"pattern": "b", "status": 599, "body": "x"},
        {"pattern": "c", "headers": {}},
    ]
    assert _load({"participants": [P], "fixtures": {"mock_routes": routes}}).fixtures == {"mock_routes": routes}
    assert _load({"participants": [P], "fixtures": {}}).fixtures == {}
    assert _load({"participants": [P], "fixtures": {"mock_routes": []}}).fixtures == {"mock_routes": []}


def test_validating_a_built_scenario_normalises_its_fixtures() -> None:
    scenario = scenarios.Scenario(
        name="b", participants=[], fixtures={"mock_routes": [{"pattern": "p", "status": 204}]}
    )
    scenarios._validate_scenario(scenario)
    assert scenario.fixtures == {"mock_routes": [{"pattern": "p", "status": 204}]}
    bad = scenarios.Scenario(name="b", participants=[], fixtures={"dialog_policy": "nope"})
    assert _error(ValueError, scenarios._validate_scenario, bad) == (
        "scenario 'b': fixtures.dialog_policy must be one of accept, dismiss, manual"
    )


# --- Python scenarios -----------------------------------------------------------------


def _py(dirs: dict[str, Path], name: str, body: str) -> Path:
    dirs["scenarios"].mkdir(parents=True, exist_ok=True)
    path = dirs["scenarios"] / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


BUILD = (
    "from octowright import scenarios\n"
    "def build():\n"
    "    return scenarios.Scenario(name='py', participants=[scenarios.Participant(persona='ann', kind='chromium', role='player')],"
    " fixtures={'dialog_policy': 'accept'})\n"
)


def test_a_python_scenario_is_refused_without_the_opt_in(dirs: dict[str, Path]) -> None:
    path = _py(dirs, "pyscen", BUILD)
    assert _error(RuntimeError, scenarios.load_python_scenario, path) == (
        f"Python scenario {path} is gated behind {PY_ENV}=1; .py scenarios execute arbitrary code at import. "
        f"Either convert to .yaml or set {PY_ENV}=1 to opt in."
    )


def test_an_opted_in_python_scenario_is_built_validated_and_audited(
    dirs: dict[str, Path], logged: LogRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PY_ENV, "1")
    path = _py(dirs, "pyscen", BUILD)
    monkeypatch.delitem(sys.modules, "octowright._scenario_pyscen", raising=False)
    scenario = scenarios.load_python_scenario(path)
    assert scenario == scenarios.Scenario(
        name="py",
        participants=[scenarios.Participant(persona="ann", kind="chromium", role="player")],
        fixtures={"dialog_policy": "accept"},
    )
    assert sys.modules["octowright._scenario_pyscen"].build().name == "py"
    assert logged.warnings == [
        (
            "scenarios.python_load_executes_arbitrary_code",
            {"path": str(path), "hint": "treat scenarios dir as trusted local config"},
        )
    ]


def test_a_python_scenario_without_build_or_returning_something_else_is_refused(
    dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PY_ENV, "1")
    nobuild = _py(dirs, "nobuild", "x = 1\n")
    assert _error(RuntimeError, scenarios.load_python_scenario, nobuild) == (
        f"Python scenario {nobuild} must define a top-level build() -> Scenario"
    )
    wrong = _py(dirs, "wrong", "def build():\n    return {'name': 'x'}\n")
    assert (
        _error(TypeError, scenarios.load_python_scenario, wrong) == f"{wrong}:build() returned dict, expected Scenario"
    )
    dup = _py(
        dirs,
        "dup",
        "from octowright import scenarios\n"
        "def build():\n"
        "    p = scenarios.Participant(persona='a', kind='chromium', role='player')\n"
        "    return scenarios.Scenario(name='d', participants=[p, p])\n",
    )
    assert _error(ValueError, scenarios.load_python_scenario, dup) == (
        "scenario 'd': duplicate (persona, kind) pair ('a', 'chromium')"
    )


def test_a_file_python_cannot_load_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PY_ENV, "1")
    path = tmp_path / "scen.txt"
    path.write_text("x = 1\n", encoding="utf-8")
    assert _error(RuntimeError, scenarios.load_python_scenario, path) == f"could not load Python scenario from {path}"


# --- by name --------------------------------------------------------------------------


def test_load_scenario_reads_yaml_and_says_where_it_looked(dirs: dict[str, Path], logged: LogRecorder) -> None:
    assert _error(FileNotFoundError, scenarios.load_scenario, "nope") == (
        f"no scenario named 'nope' in {dirs['scenarios']}; list available with `scenario_list` "
        "or drop a nope.yaml file in that directory"
    )
    dirs["scenarios"].mkdir(parents=True)
    (dirs["scenarios"] / "y.yaml").write_text(yaml.safe_dump({"participants": [P]}), encoding="utf-8")
    assert scenarios.load_scenario("y") == scenarios.Scenario(
        name="y", participants=[scenarios.Participant(persona="ann", kind="chromium", role="player")]
    )
    assert logged.warnings == []


def test_load_scenario_prefers_python_and_warns_when_both_exist(
    dirs: dict[str, Path], logged: LogRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PY_ENV, "1")
    _py(dirs, "both", BUILD)
    (dirs["scenarios"] / "both.yaml").write_text(yaml.safe_dump({"participants": [P]}), encoding="utf-8")
    monkeypatch.delitem(sys.modules, "octowright._scenario_both", raising=False)
    assert scenarios.load_scenario("both").name == "py"
    assert logged.warnings[0] == ("scenarios.both_forms_present_py_wins", {"name": "both"})
    assert [event for event, _ in logged.warnings] == [
        "scenarios.both_forms_present_py_wins",
        "scenarios.python_load_executes_arbitrary_code",
    ]


def test_load_scenario_with_only_python_does_not_warn_about_both(
    dirs: dict[str, Path], logged: LogRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PY_ENV, "1")
    _py(dirs, "only", BUILD)
    monkeypatch.delitem(sys.modules, "octowright._scenario_only", raising=False)
    assert scenarios.load_scenario("only").name == "py"
    assert [event for event, _ in logged.warnings] == ["scenarios.python_load_executes_arbitrary_code"]


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd"])
def test_load_scenario_refuses_a_name_outside_the_directory(dirs: dict[str, Path], name: str) -> None:
    message = _error(Exception, scenarios.load_scenario, name)
    assert message.startswith(f"scenario name {name!r} ")
    assert "resolves outside" in message


def test_load_scenario_refuses_a_python_file_symlinked_out_of_the_directory(
    dirs: dict[str, Path], tmp_path: Path
) -> None:
    dirs["scenarios"].mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    link = dirs["scenarios"] / "evil.py"
    link.symlink_to(outside)
    assert _error(Exception, scenarios.load_scenario, "evil") == (
        f"scenario name 'evil' {str(link)!r} resolves outside {str(dirs['scenarios'])!r}"
    )


def test_load_scenario_template_refuses_a_file_symlinked_out_of_the_directory(
    dirs: dict[str, Path], tmp_path: Path
) -> None:
    dirs["templates"].mkdir(parents=True)
    outside = tmp_path / "outside.yaml"
    outside.write_text("participants: []\n", encoding="utf-8")
    link = dirs["templates"] / "evil.yaml"
    link.symlink_to(outside)
    assert _error(Exception, scenarios.load_scenario_template, "evil", {}) == (
        f"scenario template name 'evil' {str(link)!r} resolves outside {str(dirs['templates'])!r}"
    )


def test_list_scenarios_lists_each_name_once_with_its_form(dirs: dict[str, Path]) -> None:
    assert scenarios.list_scenarios() == []
    root = dirs["scenarios"]
    root.mkdir(parents=True)
    for file_name in ("b.yaml", "a.py", "a.yaml", "notes.txt", "c.yml"):
        (root / file_name).write_text("", encoding="utf-8")
    listed = scenarios.list_scenarios()
    assert listed == [
        {"name": "a", "path": str(root / "a.py"), "form": "python", "mtime": (root / "a.py").stat().st_mtime},
        {"name": "b", "path": str(root / "b.yaml"), "form": "yaml", "mtime": (root / "b.yaml").stat().st_mtime},
    ]


# --- templates ------------------------------------------------------------------------


def _template(dirs: dict[str, Path], name: str, text: str) -> None:
    dirs["templates"].mkdir(parents=True, exist_ok=True)
    (dirs["templates"] / f"{name}.yaml").write_text(text, encoding="utf-8")


def test_a_template_is_parsed_then_substituted(dirs: dict[str, Path]) -> None:
    _template(dirs, "duo", 'participants:\n  - persona: "{{who}}"\n    kind: chromium\n    trace: "{{t}}"\n')
    assert scenarios.load_scenario_template("duo", {"who": "ann", "t": "true"}) == scenarios.Scenario(
        name="duo", participants=[scenarios.Participant(persona="ann", kind="chromium", role="player", trace=True)]
    )


def test_a_missing_template_says_where_it_looked(dirs: dict[str, Path]) -> None:
    assert _error(FileNotFoundError, scenarios.load_scenario_template, "none", {}) == (
        f"no scenario template named 'none' in {dirs['templates']}"
    )


@pytest.mark.parametrize("brk", ["\n", "\r", "\x85", "\u2028", "\u2029"])
def test_a_template_arg_with_any_yaml_line_break_is_refused(dirs: dict[str, Path], brk: str) -> None:
    _template(dirs, "duo", 'participants:\n  - persona: "{{who}}"\n    kind: chromium\n')
    assert _error(ValueError, scenarios.load_scenario_template, "duo", {"ok": "fine", "who": f"a{brk}b"}) == (
        "scenario template arg 'who' contains a newline (a YAML line break)"
    )


def test_a_template_that_is_not_yaml_before_substitution_says_how_to_quote(dirs: dict[str, Path]) -> None:
    text = "participants:\n  - persona: {{who}}\n    kind: chromium\n"
    _template(dirs, "bare", text)
    message = _error(ValueError, scenarios.load_scenario_template, "bare", {"who": "ann"})
    assert message.startswith("scenario template 'bare' is not valid YAML before substitution (")
    assert message.endswith("); " + scenarios.unquoted_placeholder_hint(text))


# --- launch resolution ----------------------------------------------------------------


def _persona(dirs: dict[str, Path], name: str, doc: dict[str, Any]) -> None:
    pdir = dirs["profiles"] / name
    pdir.mkdir(parents=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_launch_kwargs_fall_back_to_defaults_without_a_persona() -> None:
    assert scenarios.resolve_launch_kwargs(scenarios.Participant(persona="ghost", kind="webkit", role="player")) == {
        "kind": "webkit",
        "profile": "ghost",
        "url": None,
        "label": None,
        "viewport_w": None,
        "viewport_h": None,
        "stabilize": False,
        "record_video": False,
        "trace": False,
    }


def test_launch_kwargs_take_the_persona_url_unless_the_participant_sets_one(dirs: dict[str, Path]) -> None:
    _persona(dirs, "ann", {"default_url": "https://persona.test", "default_macros": ["login"]})
    p = scenarios.Participant(
        persona="ann",
        kind="chromium",
        role="player",
        viewport_w=1,
        viewport_h=2,
        stabilize=True,
        record_video=True,
        trace=True,
    )
    assert scenarios.resolve_launch_kwargs(p) == {
        "kind": "chromium",
        "profile": "ann",
        "url": "https://persona.test",
        "label": None,
        "viewport_w": 1,
        "viewport_h": 2,
        "stabilize": True,
        "record_video": True,
        "trace": True,
    }
    p.url = "https://own.test"
    assert scenarios.resolve_launch_kwargs(p)["url"] == "https://own.test"
    p.url = ""
    assert scenarios.resolve_launch_kwargs(p)["url"] == ""


def test_launch_kwargs_with_a_persona_lacking_a_url_have_none(dirs: dict[str, Path]) -> None:
    _persona(dirs, "bob", {"default_url": ""})
    assert (
        scenarios.resolve_launch_kwargs(scenarios.Participant(persona="bob", kind="chromium", role="player"))["url"]
        is None
    )


def test_startup_macros_come_from_the_participant_then_the_persona(dirs: dict[str, Path]) -> None:
    _persona(dirs, "ann", {"default_macros": ["login", "warm"]})
    _persona(dirs, "bare", {})
    own = ["mine"]
    resolved = scenarios.resolve_startup_macros(
        scenarios.Participant(persona="ann", kind="chromium", role="player", startup_macros=own)
    )
    assert resolved == ["mine"]
    assert resolved is not own
    assert (
        scenarios.resolve_startup_macros(
            scenarios.Participant(persona="ann", kind="chromium", role="player", startup_macros=[])
        )
        == []
    )
    assert scenarios.resolve_startup_macros(scenarios.Participant(persona="ann", kind="chromium", role="player")) == [
        "login",
        "warm",
    ]
    assert scenarios.resolve_startup_macros(scenarios.Participant(persona="bare", kind="chromium", role="player")) == []
    assert (
        scenarios.resolve_startup_macros(scenarios.Participant(persona="ghost", kind="chromium", role="player")) == []
    )
    assert scenarios._load_persona_or_none("ghost") is None
    assert scenarios._load_persona_or_none("ann") == personas.load_persona("ann")
