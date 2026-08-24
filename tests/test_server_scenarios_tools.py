# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import macros as octowright_macros
from octowright.server import scenarios as _scenarios


@pytest.fixture(autouse=True)
def _recordings_dir_under_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from octowright import defaults

    rec = tmp_path / "recordings"
    rec.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)


@pytest.fixture(autouse=True)
def _patch_deps(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    fake_pool = MagicMock()
    fake_spool = MagicMock()
    fake_tpool = MagicMock()
    monkeypatch.setattr(_scenarios, "pool", fake_pool)
    monkeypatch.setattr(_scenarios, "scenario_pool", fake_spool)
    monkeypatch.setattr(_scenarios, "terminal_pool", fake_tpool)
    return {"pool": fake_pool, "scenario_pool": fake_spool, "terminal_pool": fake_tpool}


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
    _patch_deps["scenario_pool"].remap_participants.assert_called_once_with(
        scenario_id="s",
        remaps=[{"old_instance_id": "a", "new_instance_id": "b"}],
        browser_pool=_patch_deps["pool"],
        terminal_pool=_patch_deps["terminal_pool"],
    )


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


@pytest.mark.anyio
async def test_scenario_run_macro_propagates_missing_role(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["scenario_pool"].run_macro = AsyncMock(
        side_effect=ValueError("scenario 'sid' has no participants with role ''")
    )

    with pytest.raises(ValueError, match="role ''"):
        await _scenarios.scenario_run_macro("sid", "m1", role="")


@pytest.mark.anyio
async def test_scenario_wait_for_sync_propagates_missing_role(_patch_deps: dict[str, MagicMock]) -> None:
    _patch_deps["scenario_pool"].wait_for_sync = AsyncMock(
        side_effect=ValueError("scenario 'sid' has no participants with role ''")
    )

    with pytest.raises(ValueError, match="role ''"):
        await _scenarios.scenario_wait_for_sync("sid", selector="#x", role="")


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
        {"role": "player", "persona": "cosmo", "instance_id": "i1", "kind": "chromium"},
        {"role": "observer", "persona": "ziggy", "instance_id": "i2", "kind": "chromium"},
    ]
    live = SimpleNamespace(
        name="demo",
        spec=SimpleNamespace(verify={"player": "macro_player"}),
        participants=participants,
    )
    _patch_deps["scenario_pool"].get.return_value = live
    _patch_deps["pool"].get.return_value = object()
    monkeypatch.setattr(octowright_macros, "run_macro", AsyncMock(return_value=None))
    monkeypatch.setattr(_scenarios.runner_mod, "_default_report_path", lambda: tmp_path / "default.xml")
    write_calls: list[tuple[list[dict[str, object]], Path, str]] = []

    def _fake_write(results: list[dict[str, object]], path: Path, *, kind: str) -> None:
        write_calls.append((results, path, kind))

    monkeypatch.setattr(_scenarios.runner_mod, "_write_junit", _fake_write)

    out = await _scenarios.scenario_run_as_test("sid", out_path=str(tmp_path / "recordings" / "report.xml"))
    assert out["total"] == 2
    assert out["passed"] == 1
    assert out["failed"] == 1
    assert out["report_path"].endswith("report.xml")
    assert write_calls and write_calls[0][2] == "scenario"


@pytest.mark.anyio
async def test_scenario_run_as_test_skips_a_capability_less_kind_cleanly(
    _patch_deps: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A participant whose adapter has no run_macro (terminal today, or any
    plugin kind that never implements it) must be skipped cleanly -- no test
    case at all -- rather than falling through to pool.get() on an instance id
    the browser pool never held. Before the fix, only kind == "terminal" was
    excluded, so a plugin participant with a verify macro declared for its
    role produced a *failing* test case (pool.get() raising, or a real
    macro-load failure against a mocked session) instead of being excluded
    the way scenario_run_macro already reports it (a clean capability
    message). Asserting only "no exception escaped" would pass either way --
    this asserts the participant produced zero test cases."""
    from octowright.plugins.registry import PluginRegistry
    from octowright.server import plugin_state

    class _NoMacroAdapter:
        def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
            return {}

    class _Descriptor:
        kind = "refkind"
        display_name = "Reference Kind"
        plugin_api_version = 1
        tool_names: frozenset[str] = frozenset()
        tool_module = None
        profile_name = None
        frontend = None

        def create_pool(self, ctx: Any) -> Any:
            raise AssertionError("not used")

        def create_scenario_adapter(self, pool: Any) -> Any:
            raise AssertionError("not used")

        def session_detail(self, session: Any) -> dict[str, Any]:
            return {}

    original = plugin_state.registry()
    reg = PluginRegistry()
    reg.register(_Descriptor(), pool="REFPOOL", adapter=_NoMacroAdapter(), discovered=None)
    plugin_state.set_registry(reg)

    participants = [{"role": "monitor", "persona": "ref-rita", "instance_id": "r1", "kind": "refkind"}]
    live = SimpleNamespace(
        name="demo",
        spec=SimpleNamespace(verify={"monitor": "verify_macro"}),
        participants=participants,
    )
    _patch_deps["scenario_pool"].get.return_value = live
    monkeypatch.setattr(_scenarios.runner_mod, "_default_report_path", lambda: tmp_path / "default.xml")
    write_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(_scenarios.runner_mod, "_write_junit", lambda *args, **kwargs: write_calls.append(args))

    try:
        out = await _scenarios.scenario_run_as_test("sid", out_path=str(tmp_path / "recordings" / "report.xml"))
    finally:
        plugin_state.set_registry(original)

    assert out["total"] == 0, "a capability-less participant must not produce any test case, passing or failing"
    assert out["results"] == []


@pytest.mark.anyio
async def test_scenario_run_as_test_rejects_out_path_outside_recordings(
    _patch_deps: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    participants = [{"role": "player", "persona": "cosmo", "instance_id": "i1", "kind": "chromium"}]
    live = SimpleNamespace(
        name="demo",
        spec=SimpleNamespace(verify={"player": "macro_player"}),
        participants=participants,
    )
    _patch_deps["scenario_pool"].get.return_value = live
    _patch_deps["pool"].get.return_value = object()
    monkeypatch.setattr(octowright_macros, "run_macro", AsyncMock(return_value=None))
    monkeypatch.setattr(_scenarios.runner_mod, "_write_junit", lambda *args, **kwargs: None)

    with pytest.raises(ValueError, match="scenario report path"):
        await _scenarios.scenario_run_as_test("sid", out_path=str(tmp_path / "outside.xml"))
