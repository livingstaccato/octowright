# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit tests for the scenario_plan MCP tool — verifies dry-run behavior:
loads scenario specs and reports the resolved per-participant launch_kwargs
and startup_macros, without touching any browser pool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from octowright import scenarios as _scenarios
from octowright.server.scenarios import scenario_plan

# ---------------------------------------------------------------------------
# fixtures (mirror tests/test_scenarios_unit.py)
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


def _write(scenarios_dir: Path, name: str, participants: list[dict[str, Any]], **extra: Any) -> None:
    doc = {"name": name, "participants": participants, **extra}
    (scenarios_dir / f"{name}.yaml").write_text(yaml.safe_dump(doc))


# ---------------------------------------------------------------------------
# basic resolution
# ---------------------------------------------------------------------------


def test_single_participant_no_persona_uses_fallback(
    scenarios_dir: Path,
    empty_personas_dir: Path,
) -> None:
    _write(scenarios_dir, "solo", [{"persona": "ghost", "kind": "webkit", "role": "player"}])
    plan = scenario_plan(name="solo")
    assert plan["name"] == "solo"
    assert plan["would_launch"] == 1
    assert len(plan["participants"]) == 1
    p = plan["participants"][0]
    assert p["persona"] == "ghost"
    assert p["kind"] == "webkit"
    assert p["role"] == "player"
    # No persona on disk, no url override -> profile=name, url=None
    assert p["launch_kwargs"]["profile"] == "ghost"
    assert p["launch_kwargs"]["url"] is None
    assert p["launch_kwargs"]["kind"] == "webkit"
    assert p["startup_macros"] == []


def test_multi_participant_mixed_engines(
    scenarios_dir: Path,
    empty_personas_dir: Path,
) -> None:
    _write(
        scenarios_dir,
        "mixed",
        [
            {"persona": "a", "kind": "webkit", "role": "player"},
            {"persona": "b", "kind": "firefox", "role": "monitor"},
            {"persona": "c", "kind": "chromium", "role": "ops"},
        ],
    )
    plan = scenario_plan(name="mixed")
    assert plan["would_launch"] == 3
    rows = plan["participants"]
    assert [r["kind"] for r in rows] == ["webkit", "firefox", "chromium"]
    assert [r["persona"] for r in rows] == ["a", "b", "c"]
    assert [r["role"] for r in rows] == ["player", "monitor", "ops"]
    # Each row carries its own launch_kwargs.
    assert rows[0]["launch_kwargs"]["kind"] == "webkit"
    assert rows[1]["launch_kwargs"]["kind"] == "firefox"
    assert rows[2]["launch_kwargs"]["kind"] == "chromium"


# ---------------------------------------------------------------------------
# url + persona resolution
# ---------------------------------------------------------------------------


def test_url_override_wins_over_persona_default_url(
    scenarios_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdir = tmp_path / "profiles"
    (pdir / "dante").mkdir(parents=True)
    (pdir / "dante" / "profile.yaml").write_text(
        yaml.safe_dump({"name": "dante", "default_url": "https://from-persona/"})
    )
    from octowright import personas as _personas

    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

    _write(
        scenarios_dir,
        "ovr",
        [
            {
                "persona": "dante",
                "kind": "webkit",
                "role": "player",
                "url": "https://override-wins/",
            }
        ],
    )
    plan = scenario_plan(name="ovr")
    assert plan["participants"][0]["launch_kwargs"]["url"] == "https://override-wins/"


def test_persona_default_macros_propagate(
    scenarios_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdir = tmp_path / "profiles"
    (pdir / "dante").mkdir(parents=True)
    (pdir / "dante" / "profile.yaml").write_text(
        yaml.safe_dump({"name": "dante", "default_macros": ["greet", "warmup"]})
    )
    from octowright import personas as _personas

    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

    _write(scenarios_dir, "macs", [{"persona": "dante", "kind": "webkit", "role": "player"}])
    plan = scenario_plan(name="macs")
    assert plan["participants"][0]["startup_macros"] == ["greet", "warmup"]


def test_explicit_empty_startup_macros_overrides_persona_defaults(
    scenarios_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdir = tmp_path / "profiles"
    (pdir / "dante").mkdir(parents=True)
    (pdir / "dante" / "profile.yaml").write_text(
        yaml.safe_dump({"name": "dante", "default_macros": ["should-not-fire"]})
    )
    from octowright import personas as _personas

    monkeypatch.setattr(_personas, "PROFILES_DIR", pdir)

    _write(
        scenarios_dir,
        "ovr2",
        [
            {
                "persona": "dante",
                "kind": "webkit",
                "role": "player",
                "startup_macros": [],
            }
        ],
    )
    plan = scenario_plan(name="ovr2")
    assert plan["participants"][0]["startup_macros"] == []


# ---------------------------------------------------------------------------
# fixtures / teardown / verify pass-through
# ---------------------------------------------------------------------------


def test_fixtures_teardown_verify_pass_through(
    scenarios_dir: Path,
    empty_personas_dir: Path,
) -> None:
    _write(
        scenarios_dir,
        "full",
        [{"persona": "a", "kind": "webkit", "role": "player"}],
        description="full pipeline",
        fixtures={"dialog_policy": "dismiss", "mock_routes": [{"pattern": "**/api", "body": "{}"}]},
        teardown={"macro": "cleanup"},
        verify={"player": "assert_player_ok"},
    )
    plan = scenario_plan(name="full")
    assert plan["description"] == "full pipeline"
    assert plan["fixtures"] == {
        "dialog_policy": "dismiss",
        "mock_routes": [{"pattern": "**/api", "body": "{}"}],
    }
    assert plan["teardown_macro"] == "cleanup"
    assert plan["verify"] == {"player": "assert_player_ok"}


# ---------------------------------------------------------------------------
# summary line
# ---------------------------------------------------------------------------


def test_summary_is_participant_summary_one_liner(
    scenarios_dir: Path,
    empty_personas_dir: Path,
) -> None:
    _write(
        scenarios_dir,
        "sumtest",
        [
            {"persona": "dante", "kind": "webkit", "role": "player"},
            {"persona": "ops", "kind": "firefox", "role": "monitor"},
        ],
    )
    plan = scenario_plan(name="sumtest")
    assert plan["summary"] == "player[dante]/webkit · monitor[ops]/firefox"


def test_would_launch_matches_participant_count(
    scenarios_dir: Path,
    empty_personas_dir: Path,
) -> None:
    _write(
        scenarios_dir,
        "count",
        [
            {"persona": "a", "kind": "webkit", "role": "r1"},
            {"persona": "b", "kind": "firefox", "role": "r2"},
            {"persona": "c", "kind": "chromium", "role": "r3"},
            {"persona": "d", "kind": "webkit", "role": "r4"},
        ],
    )
    plan = scenario_plan(name="count")
    assert plan["would_launch"] == 4
    assert len(plan["participants"]) == 4


# ---------------------------------------------------------------------------
# verifies plan does NOT touch the browser pool
# ---------------------------------------------------------------------------


class _ExplodingPool:
    """Any attribute access (including method call) raises."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"scenario_plan must not touch the browser pool (accessed {name!r})")


def test_plan_does_not_touch_pool(
    scenarios_dir: Path,
    empty_personas_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace the live `pool` and `scenario_pool` with exploding stand-ins to
    prove scenario_plan never reaches into them."""
    from octowright.server import _state

    monkeypatch.setattr(_state, "pool", _ExplodingPool())
    monkeypatch.setattr(_state, "scenario_pool", _ExplodingPool())

    _write(scenarios_dir, "isolated", [{"persona": "a", "kind": "webkit", "role": "player"}])
    plan = scenario_plan(name="isolated")
    assert plan["would_launch"] == 1
