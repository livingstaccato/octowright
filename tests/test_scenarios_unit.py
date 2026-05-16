# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit tests for octowright.scenarios — validation, loaders, resolve helpers,
and ScenarioPool error/teardown paths exercised against a stub browser pool.

No real Playwright. The stub pool implements just enough of the real
BrowserPool surface (spawn_roster / get / close) to drive the scenario
lifecycle through every error branch.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from octowright import scenarios as _scenarios
from octowright.scenarios import (
    Participant,
    Scenario,
    ScenarioPool,
    _validate_scenario,
    list_scenarios,
    load_python_scenario,
    load_scenario,
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
def empty_personas_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Make load_persona always raise FileNotFoundError so resolve_* hits the no-persona branch."""
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    from octowright import personas as _personas

    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)
    return pdir


# ---------------------------------------------------------------------------
# _validate_scenario
# ---------------------------------------------------------------------------


class TestValidateScenario:
    def test_unsupported_kind_rejected(self) -> None:
        s = Scenario(
            name="bad",
            participants=[Participant(persona="dante", kind="opera", role="player")],
        )
        with pytest.raises(ValueError, match="unsupported kind 'opera'"):
            _validate_scenario(s)

    def test_duplicate_persona_kind_rejected(self) -> None:
        s = Scenario(
            name="bad",
            participants=[
                Participant(persona="dante", kind="webkit", role="a"),
                Participant(persona="dante", kind="webkit", role="b"),  # same (persona, kind)
            ],
        )
        with pytest.raises(ValueError, match="duplicate \\(persona, kind\\)"):
            _validate_scenario(s)

    def test_same_persona_different_kind_ok(self) -> None:
        """One persona on multiple engines is fine."""
        s = Scenario(
            name="ok",
            participants=[
                Participant(persona="dante", kind="webkit", role="a"),
                Participant(persona="dante", kind="firefox", role="b"),
            ],
        )
        _validate_scenario(s)  # does not raise

    def test_distinct_personas_in_one_scenario_ok(self) -> None:
        """Multiple distinct persona identities share a single scenario.

        Validates that the (persona, kind) uniqueness rule lets two truly
        different personas coexist — the primary multi-tenant case
        (e.g. Dante vs Mortimer vs Cosmo all logging in side-by-side).
        """
        s = Scenario(
            name="three-tenants",
            participants=[
                Participant(persona="dante", kind="webkit", role="player"),
                Participant(persona="mortimer", kind="firefox", role="monitor"),
                Participant(persona="cosmo", kind="chromium", role="spectator"),
            ],
        )
        _validate_scenario(s)  # does not raise
        assert {p.persona for p in s.participants} == {"dante", "mortimer", "cosmo"}
        assert {p.role for p in s.participants} == {"player", "monitor", "spectator"}

    def test_distinct_personas_same_engine_ok(self) -> None:
        """Two different personas on the same engine is allowed —
        (persona, kind) only collides when both fields match."""
        s = Scenario(
            name="two-on-webkit",
            participants=[
                Participant(persona="dante", kind="webkit", role="a"),
                Participant(persona="ziggy", kind="webkit", role="b"),
            ],
        )
        _validate_scenario(s)  # does not raise


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


class TestLoadYamlScenario:
    def test_full_yaml_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "mini.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "name": "mini",
                    "description": "two players",
                    "participants": [
                        {"persona": "a", "kind": "webkit", "role": "player"},
                        {
                            "persona": "b",
                            "kind": "firefox",
                            "role": "monitor",
                            "url": "https://x/",
                            "startup_macros": ["greet"],
                        },
                    ],
                    "fixtures": {"dialog_policy": "dismiss"},
                    "teardown": {"macro": "cleanup"},
                    "verify": {"player": "assert_player_ok"},
                }
            )
        )
        s = load_yaml_scenario(path.read_text(), path.stem)
        assert s.name == "mini"
        assert s.description == "two players"
        assert len(s.participants) == 2
        assert s.participants[1].url == "https://x/"
        assert s.participants[1].startup_macros == ["greet"]
        assert s.fixtures == {"dialog_policy": "dismiss"}
        assert s.teardown_macro == "cleanup"
        assert s.verify == {"player": "assert_player_ok"}

    def test_empty_yaml_yields_empty_scenario(self, tmp_path: Path) -> None:
        """An empty file (parses as None) defaults to an empty scenario named after the file."""
        path = tmp_path / "blank.yaml"
        path.write_text("")
        s = load_yaml_scenario(path.read_text(), path.stem)
        assert s.name == "blank"
        assert s.participants == []

    def test_teardown_not_a_dict_yields_no_teardown_macro(self, tmp_path: Path) -> None:
        path = tmp_path / "x.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "name": "x",
                    "participants": [{"persona": "a", "kind": "webkit"}],
                    "teardown": "string-not-a-dict",
                }
            )
        )
        s = load_yaml_scenario(path.read_text(), path.stem)
        assert s.teardown_macro is None

    def test_validation_runs_on_yaml_load(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "name": "bad",
                    "participants": [{"persona": "a", "kind": "opera"}],
                }
            )
        )
        with pytest.raises(ValueError, match="unsupported kind"):
            load_yaml_scenario(path.read_text(), path.stem)


# ---------------------------------------------------------------------------
# Python loader
# ---------------------------------------------------------------------------


class TestLoadPythonScenario:
    def test_python_module_with_build_function(self, tmp_path: Path) -> None:
        path = tmp_path / "dyn.py"
        path.write_text(
            "from octowright.scenarios import Scenario, Participant\n"
            "def build():\n"
            "    return Scenario(\n"
            "        name='dyn',\n"
            "        participants=[Participant(persona='a', kind='webkit', role='r')],\n"
            "    )\n"
        )
        s = load_python_scenario(path)
        assert s.name == "dyn"
        assert len(s.participants) == 1

    def test_module_without_build_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "missing.py"
        path.write_text("# no build() function\n")
        with pytest.raises(RuntimeError, match="must define a top-level build"):
            load_python_scenario(path)

    def test_build_returning_wrong_type_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong.py"
        path.write_text("def build():\n    return {'not': 'a Scenario'}\n")
        with pytest.raises(TypeError, match="returned dict, expected Scenario"):
            load_python_scenario(path)

    def test_python_load_validates(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.py"
        path.write_text(
            "from octowright.scenarios import Scenario, Participant\n"
            "def build():\n"
            "    return Scenario(\n"
            "        name='bad',\n"
            "        participants=[Participant(persona='a', kind='opera', role='r')],\n"
            "    )\n"
        )
        with pytest.raises(ValueError, match="unsupported kind"):
            load_python_scenario(path)


# ---------------------------------------------------------------------------
# load_scenario dispatch + list_scenarios
# ---------------------------------------------------------------------------


class TestLoadScenarioDispatch:
    def test_python_wins_over_yaml(self, scenarios_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
        """When both .py and .yaml exist for the same name, the .py form is used."""
        (scenarios_dir / "both.yaml").write_text(yaml.safe_dump({"name": "from-yaml", "participants": []}))
        (scenarios_dir / "both.py").write_text(
            "from octowright.scenarios import Scenario\n"
            "def build():\n    return Scenario(name='from-py', participants=[])\n"
        )
        s = load_scenario("both")
        assert s.name == "from-py"

    def test_missing_scenario_includes_listing_hint(self, scenarios_dir: Path) -> None:
        with pytest.raises(FileNotFoundError, match="scenario_list"):
            load_scenario("ghost")

    def test_list_scenarios_reports_yaml_and_python(self, scenarios_dir: Path) -> None:
        (scenarios_dir / "alpha.yaml").write_text(yaml.safe_dump({"name": "alpha", "participants": []}))
        (scenarios_dir / "beta.py").write_text("def build(): pass\n")
        rows = list_scenarios()
        names = {(r["name"], r["form"]) for r in rows}
        assert names == {("alpha", "yaml"), ("beta", "python")}

    def test_list_scenarios_dedupes_when_both_forms_exist(self, scenarios_dir: Path) -> None:
        (scenarios_dir / "dual.yaml").write_text(yaml.safe_dump({"name": "dual", "participants": []}))
        (scenarios_dir / "dual.py").write_text("def build(): pass\n")
        rows = list_scenarios()
        # Either .py or .yaml entry wins (sorted iteration), but only one row for 'dual'.
        names = [r["name"] for r in rows]
        assert names.count("dual") == 1

    def test_list_scenarios_skips_unknown_extensions(self, scenarios_dir: Path) -> None:
        (scenarios_dir / "ignore.txt").write_text("not a scenario")
        (scenarios_dir / "real.yaml").write_text(yaml.safe_dump({"name": "real", "participants": []}))
        rows = list_scenarios()
        assert {r["name"] for r in rows} == {"real"}

    def test_list_scenarios_returns_empty_when_dir_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        missing = tmp_path / "no-such-dir"
        monkeypatch.setattr(_scenarios, "SCENARIOS_DIR", missing)
        assert list_scenarios() == []


# ---------------------------------------------------------------------------
# resolve_launch_kwargs / resolve_startup_macros
# ---------------------------------------------------------------------------


class TestResolveLaunchKwargs:
    @pytest.mark.usefixtures("empty_personas_dir")
    def test_participant_overrides_take_precedence(self) -> None:
        p = Participant(
            persona="dante",
            kind="webkit",
            role="r",
            url="https://override/",
            viewport_w=900,
            stabilize=True,
            record_video=True,
            trace=True,
        )
        kwargs = resolve_launch_kwargs(p)
        assert kwargs["url"] == "https://override/"
        assert kwargs["viewport_w"] == 900
        assert kwargs["stabilize"] is True
        assert kwargs["record_video"] is True
        assert kwargs["trace"] is True
        assert kwargs["profile"] == "dante"
        assert kwargs["kind"] == "webkit"

    def test_falls_back_to_persona_default_url_when_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_no_persona_no_url_results_in_none(self) -> None:
        p = Participant(persona="ghost", kind="webkit", role="r")
        kwargs = resolve_launch_kwargs(p)
        assert kwargs["url"] is None


class TestResolveStartupMacros:
    @pytest.mark.usefixtures("empty_personas_dir")
    def test_participant_override_wins(self) -> None:
        p = Participant(persona="dante", kind="webkit", role="r", startup_macros=["a", "b"])
        assert resolve_startup_macros(p) == ["a", "b"]

    def test_falls_back_to_persona_default_macros(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        pdir = tmp_path / "profiles"
        (pdir / "dante").mkdir(parents=True)
        (pdir / "dante" / "profile.yaml").write_text(
            yaml.safe_dump({"name": "dante", "default_macros": ["from-persona"]})
        )
        from octowright import personas as _personas

        monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

        p = Participant(persona="dante", kind="webkit", role="r")
        assert resolve_startup_macros(p) == ["from-persona"]

    @pytest.mark.usefixtures("empty_personas_dir")
    def test_no_persona_returns_empty_list(self) -> None:
        p = Participant(persona="ghost", kind="webkit", role="r")
        assert resolve_startup_macros(p) == []


# ---------------------------------------------------------------------------
# ScenarioPool.get / start / stop / run_macro — error paths via stub pool
# ---------------------------------------------------------------------------


class _StubSession:
    """Minimal session that just records dialog/mock calls and accepts the macro runner."""

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self.dialog_policy_calls: list[tuple[str, str | None]] = []
        self.mock_calls: list[dict[str, Any]] = []

    def set_dialog_policy(self, policy: str, prompt_text: str | None = None) -> None:
        self.dialog_policy_calls.append((policy, prompt_text))

    async def mock_route(self, pattern: str, **kwargs: Any) -> None:
        self.mock_calls.append({"pattern": pattern, **kwargs})


class _StubPool:
    """spawn_roster / get / close; configurable to fail on any of those."""

    def __init__(
        self,
        *,
        spawn_errors: list[dict[str, Any]] | None = None,
        spawn_launched: list[dict[str, Any]] | None = None,
        close_fails: set[str] | None = None,
        close_delay: float = 0,
    ) -> None:
        self.spawn_errors = spawn_errors or []
        self._spawn_launched = spawn_launched
        self.closed: list[str] = []
        self.close_fails = close_fails or set()
        self.close_delay = close_delay
        self.sessions: dict[str, _StubSession] = {}

    async def spawn_roster(self, specs: list[dict[str, Any]]) -> dict[str, Any]:
        if self._spawn_launched is not None:
            launched = self._spawn_launched
        else:
            launched = [
                {
                    "instance_id": f"iid-{i}",
                    "kind": s["kind"],
                    "label": None,
                    "profile": s.get("profile"),
                    "url": s.get("url"),
                    "log_path": f"/tmp/log-{i}.jsonl",
                }
                for i, s in enumerate(specs)
                if i < (len(specs) - len(self.spawn_errors))
            ]
        for entry in launched:
            self.sessions[entry["instance_id"]] = _StubSession(entry["instance_id"])
        return {"launched": launched, "errors": self.spawn_errors}

    def get(self, instance_id: str) -> _StubSession:
        if instance_id not in self.sessions:
            raise KeyError(instance_id)
        return self.sessions[instance_id]

    async def close(self, instance_id: str) -> dict[str, Any]:
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        if instance_id in self.close_fails:
            raise RuntimeError(f"forced close failure for {instance_id}")
        self.closed.append(instance_id)
        self.sessions.pop(instance_id, None)
        return {"closed": True}


def _write_trivial_scenario(scenarios_dir: Path, name: str, participants: list[dict[str, Any]], **extra: Any) -> None:
    doc = {"name": name, "participants": participants, **extra}
    (scenarios_dir / f"{name}.yaml").write_text(yaml.safe_dump(doc))


class TestScenarioPoolGet:
    def test_get_unknown_includes_start_hint_when_no_live(self) -> None:
        pool = ScenarioPool()
        with pytest.raises(KeyError, match="scenario_start"):
            pool.get("ghost")

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_get_unknown_includes_status_hint_when_others_live(self, scenarios_dir: Path) -> None:
        _write_trivial_scenario(scenarios_dir, "alive", [{"persona": "a", "kind": "webkit", "role": "r"}])
        spool = ScenarioPool()
        bp = _StubPool()
        await spool.start(name="alive", browser_pool=bp)
        with pytest.raises(KeyError, match="scenario_status"):
            spool.get("ghost")


class TestScenarioPoolStart:
    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_no_participants_raises(self, scenarios_dir: Path) -> None:
        _write_trivial_scenario(scenarios_dir, "empty", [])
        spool = ScenarioPool()
        with pytest.raises(RuntimeError, match="no participants"):
            await spool.start(name="empty", browser_pool=_StubPool())

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_partial_launch_failure_closes_succeeded_and_raises(self, scenarios_dir: Path) -> None:
        """When spawn_roster reports some errors, the partial launches must be closed before raising."""
        _write_trivial_scenario(
            scenarios_dir,
            "rough",
            [
                {"persona": "a", "kind": "webkit", "role": "r"},
                {"persona": "b", "kind": "firefox", "role": "r"},
            ],
        )
        bp = _StubPool(
            spawn_launched=[
                {
                    "instance_id": "iid-0",
                    "kind": "webkit",
                    "label": None,
                    "profile": "a",
                    "url": None,
                    "log_path": "/tmp/0.jsonl",
                }
            ],
            spawn_errors=[{"spec": {"kind": "firefox"}, "error": "boom"}],
        )
        spool = ScenarioPool()
        with pytest.raises(RuntimeError, match="failed to launch"):
            await spool.start(name="rough", browser_pool=bp)
        assert bp.closed == ["iid-0"]

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_partial_launch_close_failure_is_swallowed(self, scenarios_dir: Path) -> None:
        """Even if cleanup-close throws, the original launch failure still propagates."""
        _write_trivial_scenario(
            scenarios_dir,
            "rough2",
            [
                {"persona": "a", "kind": "webkit", "role": "r"},
                {"persona": "b", "kind": "firefox", "role": "r"},
            ],
        )
        bp = _StubPool(
            spawn_launched=[
                {
                    "instance_id": "iid-0",
                    "kind": "webkit",
                    "label": None,
                    "profile": "a",
                    "url": None,
                    "log_path": "/tmp/0.jsonl",
                }
            ],
            spawn_errors=[{"spec": {"kind": "firefox"}, "error": "boom"}],
            close_fails={"iid-0"},
        )
        spool = ScenarioPool()
        with pytest.raises(RuntimeError, match="failed to launch"):
            await spool.start(name="rough2", browser_pool=bp)


class TestScenarioPoolStop:
    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_close_failure_recorded_in_teardown_errors(self, scenarios_dir: Path) -> None:
        _write_trivial_scenario(
            scenarios_dir,
            "stoppy",
            [
                {"persona": "a", "kind": "webkit", "role": "r"},
                {"persona": "b", "kind": "firefox", "role": "r"},
            ],
        )
        bp = _StubPool(close_fails={"iid-1"})
        spool = ScenarioPool()
        live = await spool.start(name="stoppy", browser_pool=bp)
        summary = await spool.stop(scenario_id=live.scenario_id, browser_pool=bp)
        assert "iid-0" in summary["closed"]
        assert any(e["instance_id"] == "iid-1" for e in summary["teardown_errors"])

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_teardown_macro_failure_recorded(self, scenarios_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the teardown macro raises, the close still runs and the error goes into the summary."""
        _write_trivial_scenario(
            scenarios_dir,
            "td",
            [{"persona": "a", "kind": "webkit", "role": "r"}],
            teardown={"macro": "doesnt-exist"},
        )

        # Force run_macro to raise — teardown_macro lookup happens lazily inside stop().
        from octowright import macros as _macros

        async def _raising_run_macro(*, session: Any, name: str, args: dict[str, Any]) -> None:
            raise RuntimeError("teardown blew up")

        monkeypatch.setattr(_macros, "run_macro", _raising_run_macro)

        bp = _StubPool()
        spool = ScenarioPool()
        live = await spool.start(name="td", browser_pool=bp)
        summary = await spool.stop(scenario_id=live.scenario_id, browser_pool=bp)
        # Teardown error captured.
        assert any("teardown blew up" in e["error"] for e in summary["teardown_errors"])
        # Close still ran.
        assert "iid-0" in summary["closed"]

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_concurrent_stop_claims_scenario_once(self, scenarios_dir: Path) -> None:
        _write_trivial_scenario(
            scenarios_dir,
            "stoppy-once",
            [{"persona": "a", "kind": "webkit", "role": "r"}],
        )
        bp = _StubPool(close_delay=0.01)
        spool = ScenarioPool()
        live = await spool.start(name="stoppy-once", browser_pool=bp)

        results = await asyncio.gather(
            spool.stop(scenario_id=live.scenario_id, browser_pool=bp),
            spool.stop(scenario_id=live.scenario_id, browser_pool=bp),
            return_exceptions=True,
        )

        summaries = [result for result in results if isinstance(result, dict)]
        errors = [result for result in results if isinstance(result, KeyError)]
        assert len(summaries) == 1
        assert len(errors) == 1
        assert bp.closed == ["iid-0"]


class TestScenarioPoolRunMacroAndFixtures:
    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_fixtures_apply_dialog_policy_and_mock_routes(self, scenarios_dir: Path) -> None:
        _write_trivial_scenario(
            scenarios_dir,
            "fxt",
            [{"persona": "a", "kind": "webkit", "role": "r"}],
            fixtures={
                "dialog_policy": "dismiss",
                "mock_routes": [{"pattern": "**/api/time", "body": '{"now":"2026"}'}],
            },
        )
        bp = _StubPool()
        spool = ScenarioPool()
        await spool.start(name="fxt", browser_pool=bp)
        sess = bp.get("iid-0")
        assert sess.dialog_policy_calls == [("dismiss", None)]
        assert sess.mock_calls == [
            {
                "pattern": "**/api/time",
                "status": 200,
                "body": '{"now":"2026"}',
                "content_type": "application/json",
                "headers": None,
            }
        ]

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_fixture_failure_unregisters_scenario_and_closes_launches(
        self, scenarios_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_trivial_scenario(
            scenarios_dir,
            "fixture-boom",
            [{"persona": "a", "kind": "webkit", "role": "r"}],
            fixtures={"mock_routes": [{"pattern": "**/boom"}]},
        )

        async def _raise_mock_route(self: _StubSession, pattern: str, **kwargs: Any) -> None:
            raise RuntimeError("route setup failed")

        monkeypatch.setattr(_StubSession, "mock_route", _raise_mock_route)

        bp = _StubPool()
        spool = ScenarioPool()
        with pytest.raises(RuntimeError, match="route setup failed"):
            await spool.start(name="fixture-boom", browser_pool=bp)
        assert spool.list_live() == []
        assert bp.closed == ["iid-0"]

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_run_macro_collects_per_participant_results(
        self, scenarios_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_trivial_scenario(
            scenarios_dir,
            "rm",
            [
                {"persona": "a", "kind": "webkit", "role": "player"},
                {"persona": "b", "kind": "firefox", "role": "monitor"},
            ],
        )
        # First run succeeds, second raises — verify both results land in the report.
        from octowright import macros as _macros

        call_count = {"n": 0}

        async def _flaky_run_macro(*, session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("second one fails")
            return {"ok": True}

        monkeypatch.setattr(_macros, "run_macro", _flaky_run_macro)

        bp = _StubPool()
        spool = ScenarioPool()
        live = await spool.start(name="rm", browser_pool=bp)
        result = await spool.run_macro(
            scenario_id=live.scenario_id,
            macro="anything",
            browser_pool=bp,
        )
        assert result["targeted"] == 2
        assert len(result["results"]) == 2
        oks = [r["ok"] for r in result["results"]]
        assert oks.count(True) == 1
        assert oks.count(False) == 1
        # Failure entry has the error captured.
        fail = next(r for r in result["results"] if not r["ok"])
        assert "second one fails" in fail["error"]

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_run_macro_role_filter(self, scenarios_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_trivial_scenario(
            scenarios_dir,
            "rfilter",
            [
                {"persona": "a", "kind": "webkit", "role": "player"},
                {"persona": "b", "kind": "firefox", "role": "monitor"},
            ],
        )
        from octowright import macros as _macros

        async def _ok(**kwargs: Any) -> dict[str, Any]:
            return {}

        monkeypatch.setattr(_macros, "run_macro", _ok)

        bp = _StubPool()
        spool = ScenarioPool()
        live = await spool.start(name="rfilter", browser_pool=bp)
        result = await spool.run_macro(
            scenario_id=live.scenario_id,
            macro="anything",
            browser_pool=bp,
            role="player",
        )
        assert result["targeted"] == 1
        assert result["role"] == "player"


# ---------------------------------------------------------------------------
# tail edge cases
# ---------------------------------------------------------------------------


class TestScenarioPoolTail:
    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_tail_handles_missing_log_file(self, scenarios_dir: Path) -> None:
        _write_trivial_scenario(scenarios_dir, "tnone", [{"persona": "a", "kind": "webkit", "role": "r"}])
        bp = _StubPool()  # gives log_path = /tmp/log-0.jsonl, which doesn't exist
        spool = ScenarioPool()
        live = await spool.start(name="tnone", browser_pool=bp)
        result = spool.tail(scenario_id=live.scenario_id)
        assert result["events"] == []
        # Cursor for missing file is preserved at 0 (or whatever was passed).
        assert result["cursors"]["iid-0"] == 0

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_tail_advances_only_past_complete_lines(self, scenarios_dir: Path, tmp_path: Path) -> None:
        log_path = tmp_path / "log.jsonl"
        log_path.write_text('{"action":"x","ts":"1"}\n{"action":"partial",')  # last line has no \n
        _write_trivial_scenario(scenarios_dir, "tp", [{"persona": "a", "kind": "webkit", "role": "r"}])
        bp = _StubPool(
            spawn_launched=[
                {
                    "instance_id": "iid-0",
                    "kind": "webkit",
                    "label": None,
                    "profile": "a",
                    "url": None,
                    "log_path": str(log_path),
                }
            ]
        )
        spool = ScenarioPool()
        live = await spool.start(name="tp", browser_pool=bp)
        result = spool.tail(scenario_id=live.scenario_id)
        # Only the complete line was parsed.
        assert len(result["events"]) == 1
        assert result["events"][0]["action"] == "x"
        # Cursor stops at the start of the partial line so the next poll re-reads it.
        cursor = result["cursors"]["iid-0"]
        full_size = log_path.stat().st_size
        partial_len = len('{"action":"partial",')
        assert cursor == full_size - partial_len

    @pytest.mark.usefixtures("empty_personas_dir")
    @pytest.mark.asyncio
    async def test_tail_skips_malformed_json_lines(self, scenarios_dir: Path, tmp_path: Path) -> None:
        log_path = tmp_path / "log.jsonl"
        log_path.write_text('{"action":"good"}\nnot-json-at-all\n{"action":"alsogood"}\n')
        _write_trivial_scenario(scenarios_dir, "tj", [{"persona": "a", "kind": "webkit", "role": "r"}])
        bp = _StubPool(
            spawn_launched=[
                {
                    "instance_id": "iid-0",
                    "kind": "webkit",
                    "label": None,
                    "profile": "a",
                    "url": None,
                    "log_path": str(log_path),
                }
            ]
        )
        spool = ScenarioPool()
        live = await spool.start(name="tj", browser_pool=bp)
        result = spool.tail(scenario_id=live.scenario_id)
        actions = [e["action"] for e in result["events"]]
        assert actions == ["good", "alsogood"]


# ---------------------------------------------------------------------------
# startup_macros failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_macro_failure_raises_and_cleans_up(
    scenarios_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If a startup macro raises, scenario start fails and no live scenario remains."""
    pdir = tmp_path / "profiles"
    (pdir / "a").mkdir(parents=True)
    (pdir / "a" / "profile.yaml").write_text(yaml.safe_dump({"name": "a", "default_macros": ["nonexistent-startup"]}))
    from octowright import personas as _personas

    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

    _write_trivial_scenario(scenarios_dir, "sm", [{"persona": "a", "kind": "webkit", "role": "r"}])

    from octowright import macros as _macros

    async def _raising(**kwargs: Any) -> Any:
        raise RuntimeError("startup macro blew up")

    monkeypatch.setattr(_macros, "run_macro", _raising)

    bp = _StubPool()
    spool = ScenarioPool()
    with pytest.raises(RuntimeError, match="startup macro failures"):
        await spool.start(name="sm", browser_pool=bp)
    assert spool.list_live() == []
