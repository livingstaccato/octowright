# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-coverage tests for octowright.scenarios.

Pins behaviours that the broader unit suite asserts only loosely: exact
default values from `.get(key, default)` calls, exact error message
formats, log-emit calls, exact ordering of `list_scenarios`, the precise
fallback chain in `resolve_launch_kwargs` / `resolve_startup_macros`, and
the jinja-style substitution in `load_scenario_template`.

Each test pins a specific mutation that survived mutmut otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from octowright import scenarios as _scenarios
from octowright.scenarios import (
    Participant,
    Scenario,
    _validate_scenario,
    list_scenarios,
    load_python_scenario,
    load_scenario,
    load_scenario_template,
    load_yaml_scenario,
    resolve_launch_kwargs,
    resolve_startup_macros,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scenarios_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    monkeypatch.setattr(_scenarios, "SCENARIOS_DIR", sdir)
    return sdir


@pytest.fixture
def templates_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    tdir = tmp_path / "scenario-templates"
    tdir.mkdir()
    monkeypatch.setattr(_scenarios, "SCENARIO_TEMPLATES_DIR", tdir)
    return tdir


@pytest.fixture
def empty_personas_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    from octowright import personas as _personas

    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)
    return pdir


# ---------------------------------------------------------------------------
# load_yaml_scenario — per-field defaults
# ---------------------------------------------------------------------------


class TestParticipantFieldDefaults:
    """Pin every per-participant `.get(key, default)` call."""

    def _load(self, p_yaml: dict[str, Any]) -> Participant:
        doc = {"name": "x", "participants": [p_yaml]}
        s = load_yaml_scenario(yaml.safe_dump(doc), "x")
        return s.participants[0]

    def test_role_default_is_player_string(self) -> None:
        """Mutation flipping role default ('player' -> 'x') would survive without this assertion."""
        p = self._load({"persona": "a", "kind": "webkit"})
        assert p.role == "player"

    def test_role_explicit_value_preserved(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit", "role": "monitor"})
        assert p.role == "monitor"

    def test_url_default_is_none(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit"})
        assert p.url is None

    def test_startup_macros_default_is_none(self) -> None:
        """`p.get("startup_macros")` must yield None (not [] or '') when key absent."""
        p = self._load({"persona": "a", "kind": "webkit"})
        assert p.startup_macros is None

    def test_startup_macros_explicit_value_preserved(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit", "startup_macros": ["m1", "m2"]})
        assert p.startup_macros == ["m1", "m2"]

    def test_viewport_w_default_is_none(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit"})
        assert p.viewport_w is None

    def test_viewport_w_explicit_value_preserved(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit", "viewport_w": 1280})
        assert p.viewport_w == 1280

    def test_viewport_h_default_is_none(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit"})
        assert p.viewport_h is None

    def test_viewport_h_explicit_value_preserved(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit", "viewport_h": 720})
        assert p.viewport_h == 720

    def test_stabilize_default_is_none(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit"})
        assert p.stabilize is None

    def test_stabilize_explicit_true_preserved(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit", "stabilize": True})
        assert p.stabilize is True

    def test_stabilize_explicit_false_preserved(self) -> None:
        """Explicit False must round-trip — important since False vs None changes behavior."""
        p = self._load({"persona": "a", "kind": "webkit", "stabilize": False})
        assert p.stabilize is False

    def test_record_video_default_is_none(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit"})
        assert p.record_video is None

    def test_record_video_explicit_true_preserved(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit", "record_video": True})
        assert p.record_video is True

    def test_trace_default_is_none(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit"})
        assert p.trace is None

    def test_trace_explicit_true_preserved(self) -> None:
        p = self._load({"persona": "a", "kind": "webkit", "trace": True})
        assert p.trace is True


# ---------------------------------------------------------------------------
# load_yaml_scenario — top-level defaults
# ---------------------------------------------------------------------------


class TestScenarioFieldDefaults:
    def test_name_falls_back_to_passed_name(self) -> None:
        """When YAML omits 'name', the function-arg name is used verbatim."""
        s = load_yaml_scenario(yaml.safe_dump({"participants": []}), "fallback-name")
        assert s.name == "fallback-name"

    def test_name_in_yaml_overrides_passed_name(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "in-yaml", "participants": []}), "ignored")
        assert s.name == "in-yaml"

    def test_description_default_is_none(self) -> None:
        """Mutation flipping default to '' or 'x' would survive without this."""
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": []}), "x")
        assert s.description is None

    def test_description_explicit_value_preserved(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "description": "d", "participants": []}), "x")
        assert s.description == "d"

    def test_fixtures_default_is_empty_dict(self) -> None:
        """Mutation `or {}` -> `or None` would survive without asserting type."""
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": []}), "x")
        assert s.fixtures == {}
        assert isinstance(s.fixtures, dict)

    def test_fixtures_explicit_dict_preserved(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": [], "fixtures": {"k": "v"}}), "x")
        assert s.fixtures == {"k": "v"}

    def test_fixtures_null_treated_as_empty(self) -> None:
        """`fixtures: ~` (null) hits the `or {}` fallback."""
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": [], "fixtures": None}), "x")
        assert s.fixtures == {}

    def test_verify_default_is_empty_dict(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": []}), "x")
        assert s.verify == {}
        assert isinstance(s.verify, dict)

    def test_verify_explicit_dict_preserved(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": [], "verify": {"player": "ok"}}), "x")
        assert s.verify == {"player": "ok"}

    def test_teardown_macro_default_is_none(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": []}), "x")
        assert s.teardown_macro is None

    def test_teardown_macro_when_teardown_dict_has_macro(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": [], "teardown": {"macro": "cleanup"}}), "x")
        assert s.teardown_macro == "cleanup"

    def test_teardown_dict_without_macro_yields_none(self) -> None:
        """`teardown_raw.get("macro")` on a dict-without-macro returns None precisely."""
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": [], "teardown": {"other": "val"}}), "x")
        assert s.teardown_macro is None

    def test_teardown_string_yields_none(self) -> None:
        """The `if isinstance(teardown_raw, dict) else None` ternary's else branch."""
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": [], "teardown": "string-val"}), "x")
        assert s.teardown_macro is None

    def test_teardown_list_yields_none(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": [], "teardown": ["a", "b"]}), "x")
        assert s.teardown_macro is None

    def test_teardown_int_yields_none(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump({"name": "x", "participants": [], "teardown": 42}), "x")
        assert s.teardown_macro is None

    def test_participants_default_is_empty_list(self) -> None:
        """Empty `raw.get('participants', [])` produces no participants."""
        s = load_yaml_scenario(yaml.safe_dump({"name": "x"}), "x")
        assert s.participants == []
        assert isinstance(s.participants, list)


# ---------------------------------------------------------------------------
# load_yaml_scenario — non-dict / bizarre top-level inputs
# ---------------------------------------------------------------------------


class TestYamlNonDictRoot:
    def test_yaml_top_level_string_treated_as_empty(self) -> None:
        """`if not isinstance(raw, dict): raw = {}` — string root yields empty scenario."""
        s = load_yaml_scenario("just a string", "from-name")
        assert s.name == "from-name"
        assert s.participants == []

    def test_yaml_top_level_list_treated_as_empty(self) -> None:
        s = load_yaml_scenario(yaml.safe_dump([1, 2, 3]), "from-name")
        assert s.name == "from-name"
        assert s.participants == []

    def test_yaml_none_treated_as_empty(self) -> None:
        s = load_yaml_scenario("", "blank")
        assert s.name == "blank"
        assert s.participants == []

    def test_yaml_non_mapping_root_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """A list/scalar at the YAML root is almost certainly a hand-edit
        mistake. Silently resetting to {} produced cryptic 'no participants'
        errors downstream. The fallback must warn so the operator can find
        the real cause."""
        import logging

        with caplog.at_level(logging.WARNING, logger="octowright.scenarios"):
            load_yaml_scenario(yaml.safe_dump([1, 2, 3]), "list-root")
        assert any("scenarios.yaml_not_mapping" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# _validate_scenario exact messages
# ---------------------------------------------------------------------------


class TestValidateScenarioMessages:
    def test_unsupported_kind_message_contains_scenario_and_kind(self) -> None:
        """Error message must include both scenario name and offending kind in repr form."""
        s = Scenario(
            name="my-scenario",
            participants=[Participant(persona="dante", kind="opera", role="player")],
        )
        with pytest.raises(ValueError) as exc:
            _validate_scenario(s)
        msg = str(exc.value)
        assert "'my-scenario'" in msg  # name in repr
        assert "'opera'" in msg  # kind in repr
        assert "unsupported kind" in msg

    def test_duplicate_message_contains_pair_tuple(self) -> None:
        s = Scenario(
            name="dup-scn",
            participants=[
                Participant(persona="dante", kind="webkit", role="player"),
                Participant(persona="dante", kind="webkit", role="monitor"),
            ],
        )
        with pytest.raises(ValueError) as exc:
            _validate_scenario(s)
        msg = str(exc.value)
        assert "'dup-scn'" in msg
        assert "duplicate (persona, kind)" in msg
        assert "'dante'" in msg
        assert "'webkit'" in msg

    def test_validation_passes_for_zero_participants(self) -> None:
        """An empty roster validates — the 'no participants' check is in start(), not here."""
        s = Scenario(name="empty", participants=[])
        _validate_scenario(s)  # must not raise


# ---------------------------------------------------------------------------
# load_python_scenario error messages
# ---------------------------------------------------------------------------


class TestLoadPythonScenarioMessages:
    @pytest.fixture(autouse=True)
    def _allow_py_scenarios(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # These tests cover error-message wording for the post-gate code
        # path; opt in so they don't trip the default-deny env-var gate.
        monkeypatch.setenv("OCTOWRIGHT_ALLOW_PY_SCENARIOS", "1")

    def test_missing_build_message_includes_path_and_arrow(self, tmp_path: Path) -> None:
        path = tmp_path / "no_build.py"
        path.write_text("# nothing here\n")
        with pytest.raises(RuntimeError) as exc:
            load_python_scenario(path)
        msg = str(exc.value)
        assert "must define a top-level build() -> Scenario" in msg
        assert str(path) in msg

    def test_wrong_return_type_includes_actual_type_name(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong_type.py"
        path.write_text("def build():\n    return 'a string'\n")
        with pytest.raises(TypeError) as exc:
            load_python_scenario(path)
        msg = str(exc.value)
        assert "returned str" in msg
        assert "expected Scenario" in msg
        assert str(path) in msg

    def test_wrong_return_type_int_uses_int_typename(self, tmp_path: Path) -> None:
        path = tmp_path / "ret_int.py"
        path.write_text("def build():\n    return 42\n")
        with pytest.raises(TypeError, match="returned int, expected Scenario"):
            load_python_scenario(path)

    def test_wrong_return_type_list_uses_list_typename(self, tmp_path: Path) -> None:
        path = tmp_path / "ret_list.py"
        path.write_text("def build():\n    return []\n")
        with pytest.raises(TypeError, match="returned list, expected Scenario"):
            load_python_scenario(path)


# ---------------------------------------------------------------------------
# load_scenario error message + log-emit
# ---------------------------------------------------------------------------


class TestLoadScenarioErrors:
    def test_not_found_message_mentions_dir_and_hint(self, scenarios_dir: Path) -> None:
        with pytest.raises(FileNotFoundError) as exc:
            load_scenario("nonexistent-scn")
        msg = str(exc.value)
        assert "'nonexistent-scn'" in msg
        assert str(scenarios_dir) in msg
        assert "scenario_list" in msg
        assert ".yaml" in msg


# ---------------------------------------------------------------------------
# load_scenario_template
# ---------------------------------------------------------------------------


class TestLoadScenarioTemplate:
    def test_missing_template_message_includes_dir(self, templates_dir: Path) -> None:
        with pytest.raises(FileNotFoundError) as exc:
            load_scenario_template("ghost", {})
        msg = str(exc.value)
        assert "'ghost'" in msg
        assert str(templates_dir) in msg

    def test_jinja_style_substitution_replaces_placeholder(self, templates_dir: Path) -> None:
        """`{{key}}` is replaced with str(value) of the matching arg."""
        (templates_dir / "tpl.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "tpl",
                    "participants": [{"persona": "{{user}}", "kind": "webkit", "role": "player"}],
                }
            )
        )
        s = load_scenario_template("tpl", {"user": "cosmo"})
        assert s.participants[0].persona == "cosmo"

    def test_missing_arg_leaves_placeholder_unsubstituted(self, templates_dir: Path) -> None:
        """No arg for `{{name}}` — placeholder remains literally in the parsed YAML."""
        (templates_dir / "tpl2.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "tpl2",
                    "participants": [{"persona": "{{nope}}", "kind": "webkit", "role": "player"}],
                }
            )
        )
        s = load_scenario_template("tpl2", {})
        assert s.participants[0].persona == "{{nope}}"

    def test_substitution_uses_str_of_value(self, templates_dir: Path) -> None:
        """Non-string args get str()-ified."""
        (templates_dir / "num.yaml").write_text('name: num\nparticipants: []\nfixtures:\n  count: "{{n}}"\n')
        s = load_scenario_template("num", {"n": 5})
        assert s.fixtures == {"count": "5"}

    def test_substitution_only_replaces_double_braces(self, templates_dir: Path) -> None:
        """Single brace {x} should NOT be substituted; only {{x}}."""
        (templates_dir / "single.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "single",
                    "participants": [{"persona": "{user}", "kind": "webkit", "role": "player"}],
                }
            )
        )
        s = load_scenario_template("single", {"user": "cosmo"})
        assert s.participants[0].persona == "{user}"


# ---------------------------------------------------------------------------
# list_scenarios — sorting + dedup
# ---------------------------------------------------------------------------


class TestListScenariosOrdering:
    def test_results_sorted_by_filename_alphabetical(self, scenarios_dir: Path) -> None:
        """`sorted(SCENARIOS_DIR.iterdir())` — ordering must be alphabetical by filename."""
        for n in ("zulu", "alpha", "mike"):
            (scenarios_dir / f"{n}.yaml").write_text(yaml.safe_dump({"name": n, "participants": []}))
        rows = list_scenarios()
        names = [r["name"] for r in rows]
        assert names == ["alpha", "mike", "zulu"]

    def test_dedup_first_form_wins(self, scenarios_dir: Path) -> None:
        """When both forms exist, `.py` < `.yaml` alphabetically, so .py reports first."""
        (scenarios_dir / "dual.py").write_text("def build(): pass\n")
        (scenarios_dir / "dual.yaml").write_text(yaml.safe_dump({"name": "dual", "participants": []}))
        rows = list_scenarios()
        # The first iteration order encounter wins.
        dual_rows = [r for r in rows if r["name"] == "dual"]
        assert len(dual_rows) == 1
        # `.py` sorts before `.yaml`, so we expect the python form to be reported.
        assert dual_rows[0]["form"] == "python"

    def test_form_value_for_yaml_is_yaml(self, scenarios_dir: Path) -> None:
        (scenarios_dir / "y.yaml").write_text(yaml.safe_dump({"name": "y", "participants": []}))
        rows = list_scenarios()
        assert rows[0]["form"] == "yaml"

    def test_form_value_for_python_is_python(self, scenarios_dir: Path) -> None:
        (scenarios_dir / "p.py").write_text("def build(): pass\n")
        rows = list_scenarios()
        assert rows[0]["form"] == "python"

    def test_path_in_row_is_full_path(self, scenarios_dir: Path) -> None:
        """The 'path' field is the absolute string path, not just the filename."""
        (scenarios_dir / "x.yaml").write_text(yaml.safe_dump({"name": "x", "participants": []}))
        rows = list_scenarios()
        assert rows[0]["path"] == str(scenarios_dir / "x.yaml")

    def test_mtime_is_a_float(self, scenarios_dir: Path) -> None:
        (scenarios_dir / "x.yaml").write_text(yaml.safe_dump({"name": "x", "participants": []}))
        rows = list_scenarios()
        assert isinstance(rows[0]["mtime"], float)

    def test_skips_dotfiles_and_subdirs(self, scenarios_dir: Path) -> None:
        """Files without `.yaml`/`.py` suffix (including dotfiles, subdirs) are dropped."""
        (scenarios_dir / ".hidden").write_text("")
        (scenarios_dir / "subdir").mkdir()
        (scenarios_dir / "real.yaml").write_text(yaml.safe_dump({"name": "real", "participants": []}))
        rows = list_scenarios()
        assert {r["name"] for r in rows} == {"real"}


# ---------------------------------------------------------------------------
# resolve_launch_kwargs — fallback chain
# ---------------------------------------------------------------------------


class TestResolveLaunchKwargsBranches:
    @pytest.mark.usefixtures("empty_personas_dir")
    def test_label_is_always_none(self) -> None:
        """Label is hardcoded None in the kwargs dict — pin it."""
        p = Participant(persona="dante", kind="webkit", role="r")
        kwargs = resolve_launch_kwargs(p)
        assert kwargs["label"] is None

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_kind_passed_through_unchanged(self) -> None:
        for kind in ("chromium", "firefox", "webkit"):
            p = Participant(persona="x", kind=kind, role="r")
            assert resolve_launch_kwargs(p)["kind"] == kind

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_profile_is_persona_name(self) -> None:
        p = Participant(persona="dante", kind="webkit", role="r")
        assert resolve_launch_kwargs(p)["profile"] == "dante"

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_stabilize_default_when_unset_is_false(self) -> None:
        """`p.stabilize is not None else False` — the False default is load-bearing."""
        p = Participant(persona="x", kind="webkit", role="r")
        assert resolve_launch_kwargs(p)["stabilize"] is False

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_stabilize_explicit_false_preserved(self) -> None:
        """An explicit False from the participant must survive through the ternary."""
        p = Participant(persona="x", kind="webkit", role="r", stabilize=False)
        assert resolve_launch_kwargs(p)["stabilize"] is False

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_record_video_default_when_unset_is_false(self) -> None:
        p = Participant(persona="x", kind="webkit", role="r")
        assert resolve_launch_kwargs(p)["record_video"] is False

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_record_video_explicit_false_preserved(self) -> None:
        p = Participant(persona="x", kind="webkit", role="r", record_video=False)
        assert resolve_launch_kwargs(p)["record_video"] is False

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_trace_default_when_unset_is_false(self) -> None:
        p = Participant(persona="x", kind="webkit", role="r")
        assert resolve_launch_kwargs(p)["trace"] is False

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_trace_explicit_false_preserved(self) -> None:
        p = Participant(persona="x", kind="webkit", role="r", trace=False)
        assert resolve_launch_kwargs(p)["trace"] is False

    def test_persona_default_url_is_used_when_participant_url_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        pdir = tmp_path / "profiles"
        (pdir / "dante").mkdir(parents=True)
        (pdir / "dante" / "profile.yaml").write_text(
            yaml.safe_dump({"name": "dante", "default_url": "https://from-persona/"})
        )
        from octowright import personas as _personas

        monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

        p = Participant(persona="dante", kind="webkit", role="r")
        kwargs = resolve_launch_kwargs(p)
        assert kwargs["url"] == "https://from-persona/"

    def test_participant_url_wins_over_persona_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """When participant has its own URL, persona's default_url is ignored."""
        pdir = tmp_path / "profiles"
        (pdir / "dante").mkdir(parents=True)
        (pdir / "dante" / "profile.yaml").write_text(
            yaml.safe_dump({"name": "dante", "default_url": "https://persona/"})
        )
        from octowright import personas as _personas

        monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

        p = Participant(persona="dante", kind="webkit", role="r", url="https://override/")
        assert resolve_launch_kwargs(p)["url"] == "https://override/"

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_viewport_passes_through_when_set(self) -> None:
        p = Participant(persona="x", kind="webkit", role="r", viewport_w=1920, viewport_h=1080)
        kwargs = resolve_launch_kwargs(p)
        assert kwargs["viewport_w"] == 1920
        assert kwargs["viewport_h"] == 1080

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_viewport_defaults_to_none_when_unset(self) -> None:
        p = Participant(persona="x", kind="webkit", role="r")
        kwargs = resolve_launch_kwargs(p)
        assert kwargs["viewport_w"] is None
        assert kwargs["viewport_h"] is None


# ---------------------------------------------------------------------------
# resolve_startup_macros — return-list semantics
# ---------------------------------------------------------------------------


class TestResolveStartupMacrosBranches:
    @pytest.mark.usefixtures("empty_personas_dir")
    def test_returns_a_copy_not_the_original_list(self) -> None:
        """The participant's list must not be aliased — `list(p.startup_macros)` makes a copy."""
        original = ["a", "b"]
        p = Participant(persona="x", kind="webkit", role="r", startup_macros=original)
        result = resolve_startup_macros(p)
        assert result == ["a", "b"]
        assert result is not original
        # Mutating the result should not touch the participant.
        result.append("c")
        assert original == ["a", "b"]

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_explicit_empty_list_is_preserved(self) -> None:
        """An explicit [] from the participant takes precedence over persona defaults."""
        p = Participant(persona="x", kind="webkit", role="r", startup_macros=[])
        assert resolve_startup_macros(p) == []

    def test_persona_default_macros_returned_as_copy(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pdir = tmp_path / "profiles"
        (pdir / "dante").mkdir(parents=True)
        (pdir / "dante" / "profile.yaml").write_text(yaml.safe_dump({"name": "dante", "default_macros": ["m1", "m2"]}))
        from octowright import personas as _personas

        monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

        p = Participant(persona="dante", kind="webkit", role="r")
        result = resolve_startup_macros(p)
        assert result == ["m1", "m2"]
        # Reload persona — make sure resolve produces independent copies.
        result.append("m3")
        assert resolve_startup_macros(p) == ["m1", "m2"]

    def test_persona_with_no_default_macros_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`persona.default_macros or []` — None default_macros yields [] precisely."""
        pdir = tmp_path / "profiles"
        (pdir / "dante").mkdir(parents=True)
        (pdir / "dante" / "profile.yaml").write_text(yaml.safe_dump({"name": "dante"}))
        from octowright import personas as _personas

        monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

        p = Participant(persona="dante", kind="webkit", role="r")
        assert resolve_startup_macros(p) == []
