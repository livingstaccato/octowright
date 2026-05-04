# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server import scenarios as _scenarios


@pytest.fixture(autouse=True)
def _patch_deps(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    fake_pool = MagicMock()
    fake_spool = MagicMock()
    monkeypatch.setattr(_scenarios, "pool", fake_pool)
    monkeypatch.setattr(_scenarios, "scenario_pool", fake_spool)
    return {"pool": fake_pool, "scenario_pool": fake_spool}


@pytest.mark.anyio
async def test_scenario_start_returns_shape(_patch_deps: dict[str, MagicMock]) -> None:
    live = MagicMock()
    live.scenario_id = "scen1"
    live.name = "demo"
    live.participants = [{"instance_id": "a"}]
    _patch_deps["scenario_pool"].start = AsyncMock(return_value=live)
    out = await _scenarios.scenario_start("demo")
    assert out["scenario_id"] == "scen1"
    assert out["participants"][0]["instance_id"] == "a"


def test_scenario_remap_participants_forwards(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["scenario_pool"].remap_participants.return_value = {"scenario_id": "s", "count": 1, "applied": []}
    out = _scenarios.scenario_remap_participants("s", [{"old_instance_id": "a", "new_instance_id": "b"}])
    assert out["count"] == 1
    _patch_deps["scenario_pool"].remap_participants.assert_called_once()


def test_scenario_list_and_status(_patch_deps: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_scenarios.scenario_mod, "list_scenarios", lambda: [{"name": "demo"}])
    _patch_deps["scenario_pool"].list_live.return_value = [{"scenario_id": "s1", "participants": []}]
    monkeypatch.setattr(_scenarios.fmt, "scenario_summary", lambda live: f"{len(live)} live")
    assert _scenarios.scenario_list() == [{"name": "demo"}]
    out = _scenarios.scenario_status()
    assert out["summary"] == "1 live"
    assert out["count"] == 1


@pytest.mark.anyio
async def test_scenario_spawn_template_and_stop_run_wait_tail(_patch_deps: dict[str, MagicMock]) -> None:
    spec = SimpleNamespace(name="templ")
    _scenarios.scenario_mod.load_scenario_template = MagicMock(return_value=spec)
    live = SimpleNamespace(scenario_id="sid", name="templ", participants=[{"instance_id": "i1"}])
    _patch_deps["scenario_pool"].start = AsyncMock(return_value=live)
    _patch_deps["scenario_pool"].stop = AsyncMock(return_value={"scenario_id": "sid", "closed": 1})
    _patch_deps["scenario_pool"].run_macro = AsyncMock(return_value={"ok": True, "results": []})
    _patch_deps["scenario_pool"].wait_for_sync = AsyncMock(return_value={"ok": True})
    _patch_deps["scenario_pool"].tail.return_value = {"events": [], "cursors": {"i1": 0}}

    spawned = await _scenarios.scenario_spawn_template("t1", {"k": "v"})
    assert spawned["scenario_id"] == "sid"
    stopped = await _scenarios.scenario_stop("sid")
    assert stopped["closed"] == 1
    ran = await _scenarios.scenario_run_macro("sid", "m1", role="player", args={"a": 1})
    assert ran["ok"] is True
    synced = await _scenarios.scenario_wait_for_sync("sid", selector="#x", timeout_ms=100)
    assert synced["ok"] is True
    tailed = _scenarios.scenario_tail("sid", {"i1": 0})
    assert tailed["cursors"]["i1"] == 0


def test_scenario_participants_filter(_patch_deps: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch) -> None:
    live = SimpleNamespace(
        participants=[
            {"role": "player", "persona": "a"},
            {"role": "observer", "persona": "b"},
        ]
    )
    _patch_deps["scenario_pool"].get.return_value = live
    monkeypatch.setattr(_scenarios.fmt, "participant_summary", lambda rows: f"{len(rows)} participants")
    out = _scenarios.scenario_participants("sid", role="player")
    assert out["count"] == 1
    assert out["participants"][0]["persona"] == "a"


@pytest.mark.anyio
async def test_scenario_run_as_test_requires_verify(_patch_deps: dict[str, MagicMock]) -> None:
    live = SimpleNamespace(name="demo", spec=SimpleNamespace(verify={}), participants=[])
    _patch_deps["scenario_pool"].get.return_value = live
    with pytest.raises(RuntimeError, match="declares no verify macros"):
        await _scenarios.scenario_run_as_test("sid")


@pytest.mark.anyio
async def test_scenario_run_as_test_success_and_missing_macro(
    _patch_deps: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    participants = [
        {"role": "player", "persona": "alice", "instance_id": "i1"},
        {"role": "observer", "persona": "bob", "instance_id": "i2"},
    ]
    live = SimpleNamespace(
        name="demo",
        spec=SimpleNamespace(verify={"player": "macro_player"}),
        participants=participants,
    )
    _patch_deps["scenario_pool"].get.return_value = live
    _patch_deps["pool"].get.return_value = object()
    monkeypatch.setattr(_scenarios.macro_mod, "run_macro", AsyncMock(return_value=None))
    monkeypatch.setattr(_scenarios.runner_mod, "_default_report_path", lambda: tmp_path / "default.xml")
    write_calls: list[tuple[list[dict[str, object]], Path, str]] = []

    def _fake_write(results: list[dict[str, object]], path: Path, *, kind: str) -> None:
        write_calls.append((results, path, kind))

    monkeypatch.setattr(_scenarios.runner_mod, "_write_junit", _fake_write)

    out = await _scenarios.scenario_run_as_test("sid", out_path=str(tmp_path / "report.xml"))
    assert out["total"] == 2
    assert out["passed"] == 1
    assert out["failed"] == 1
    assert out["report_path"].endswith("report.xml")
    assert write_calls and write_calls[0][2] == "scenario"
