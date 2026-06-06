# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.scenarios_pool import LiveScenario, ScenarioPool, _apply_fixtures, _run_startup_macros


@dataclass
class _ParticipantSpec:
    persona: str
    role: str


@dataclass
class _Spec:
    name: str
    participants: list[_ParticipantSpec]
    fixtures: dict[str, Any]
    teardown_macro: str | None = None


class _Session:
    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self.page = type("P", (), {"url": "https://x"})()
        self.dialogs: list[str] = []
        self.routes: list[str] = []

    async def wait_for(self, selector=None, text=None, timeout_ms=None):
        return None

    async def mock_route(self, pattern: str, **kwargs):
        self.routes.append(pattern)

    def set_dialog_policy(self, policy: str):
        self.dialogs.append(policy)


class _Pool:
    def __init__(self) -> None:
        self.sessions = {"a": _Session("a"), "b": _Session("b")}
        self.closed: list[tuple[str, bool]] = []
        self.spawn_error = False

    async def spawn_roster(self, _reqs):
        if self.spawn_error:
            return {"launched": [{"instance_id": "a", "log_path": "a.log"}], "errors": ["boom"]}
        return {
            "launched": [
                {"instance_id": "a", "log_path": "a.log"},
                {"instance_id": "b", "log_path": "b.log"},
            ],
            "errors": [],
        }

    def get(self, instance_id: str):
        return self.sessions[instance_id]

    async def close(self, instance_id: str, *, force: bool = False):
        self.closed.append((instance_id, force))


def _live() -> LiveScenario:
    return LiveScenario(
        scenario_id="sid",
        name="demo",
        spec=_Spec("demo", [_ParticipantSpec("cosmo", "r1")], fixtures={}),
        participants=[{"instance_id": "a", "persona": "cosmo", "role": "r1", "kind": "chromium", "log_path": "a.log"}],
    )


def test_get_list_and_remap_errors() -> None:
    sp = ScenarioPool()
    with pytest.raises(KeyError):
        sp.get("missing")

    live = _live()
    sp._live[live.scenario_id] = live
    bp = SimpleNamespace(
        maybe_get=lambda instance_id: SimpleNamespace(kind="chromium", profile="cosmo") if instance_id == "x" else None
    )
    assert sp.list_live()[0]["scenario_id"] == "sid"

    out = sp.remap_participant(scenario_id="sid", old_instance_id="a", new_instance_id="x", browser_pool=bp)
    assert out["new_instance_id"] == "x"

    with pytest.raises(ValueError):
        sp.remap_participant(scenario_id="sid", old_instance_id="nope", new_instance_id="x", browser_pool=bp)


def test_public_live_lookup_helpers() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    assert sp.has_live(live.scenario_id) is True
    assert sp.maybe_get(live.scenario_id) is live
    assert sp.has_live("missing") is False
    assert sp.maybe_get("missing") is None


def test_remap_participants_validation() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live
    browser_pool = SimpleNamespace(
        maybe_get=lambda instance_id: SimpleNamespace(kind="chromium", profile="cosmo") if instance_id == "b" else None
    )

    result = sp.remap_participants(
        scenario_id="sid",
        remaps=[{"old_instance_id": "a", "new_instance_id": "b", "role": "r1"}],
        browser_pool=browser_pool,
    )
    assert result["count"] == 1

    with pytest.raises(ValueError):
        sp.remap_participants(scenario_id="sid", remaps=[{"old_instance_id": "", "new_instance_id": "b"}])


def test_remap_participant_requires_live_replacement() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    with pytest.raises(ValueError, match="is not live"):
        sp.remap_participant(
            scenario_id="sid",
            old_instance_id="a",
            new_instance_id="missing",
            browser_pool=SimpleNamespace(maybe_get=lambda _instance_id: None),
        )


def test_remap_participant_rejects_kind_mismatch() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    with pytest.raises(ValueError, match="expected 'chromium'"):
        sp.remap_participant(
            scenario_id="sid",
            old_instance_id="a",
            new_instance_id="b",
            browser_pool=SimpleNamespace(
                maybe_get=lambda instance_id: (
                    SimpleNamespace(kind="firefox", profile="cosmo") if instance_id == "b" else None
                )
            ),
        )


def test_remap_participant_rejects_profile_mismatch() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    with pytest.raises(ValueError, match="expected 'cosmo'"):
        sp.remap_participant(
            scenario_id="sid",
            old_instance_id="a",
            new_instance_id="b",
            browser_pool=SimpleNamespace(
                maybe_get=lambda instance_id: (
                    SimpleNamespace(kind="chromium", profile="ziggy") if instance_id == "b" else None
                )
            ),
        )


@pytest.mark.anyio
async def test_start_stop_tail_macro_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.scenarios as scenarios_mod
    import octowright.scenarios_pool as pool_mod

    sp = ScenarioPool()
    pool = _Pool()
    spec = _Spec(
        name="demo",
        participants=[_ParticipantSpec("cosmo", "r1"), _ParticipantSpec("ziggy", "r2")],
        fixtures={"dialog_policy": "dismiss", "mock_routes": [{"pattern": "**/api"}]},
        teardown_macro="bye",
    )

    monkeypatch.setattr(scenarios_mod, "load_scenario", lambda name: spec)
    monkeypatch.setattr(scenarios_mod, "resolve_launch_kwargs", lambda p: {"persona": p.persona})
    monkeypatch.setattr(scenarios_mod, "resolve_startup_macros", lambda p: ["boot"])

    calls: list[tuple[str, str]] = []

    async def _run_macro(session, name, args):
        calls.append((session.instance_id, name))

    monkeypatch.setattr(pool_mod, "_run_startup_macros", _run_startup_macros)
    monkeypatch.setattr(pool_mod, "_apply_fixtures", _apply_fixtures)
    import octowright.macros as macros_mod

    monkeypatch.setattr(macros_mod, "run_macro", _run_macro)

    live = await sp.start(name="demo", browser_pool=pool)
    assert live.name == "demo"
    assert sp.get(live.scenario_id).scenario_id == live.scenario_id

    import octowright.recorder as recorder_mod

    monkeypatch.setattr(recorder_mod, "tail_log", lambda p, c: ([{"ev": 1}], c + 1, False))
    t = sp.tail(scenario_id=live.scenario_id)
    assert t["events"]

    rm = await sp.run_macro(scenario_id=live.scenario_id, macro="m", browser_pool=pool)
    assert rm["targeted"] == 2

    ws = await sp.wait_for_sync(scenario_id=live.scenario_id, browser_pool=pool, selector="#x")
    assert ws["targeted"] == 2

    summary = await sp.stop(scenario_id=live.scenario_id, browser_pool=pool)
    assert set(summary["closed"]) == {"a", "b"}
    assert set(pool.closed[-2:]) == {("a", True), ("b", True)}


@pytest.mark.anyio
async def test_start_launch_failure_closes_partials(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.scenarios as scenarios_mod

    sp = ScenarioPool()
    pool = _Pool()
    pool.spawn_error = True
    spec = _Spec(
        name="demo",
        participants=[_ParticipantSpec("cosmo", "r1"), _ParticipantSpec("ziggy", "r2")],
        fixtures={},
    )

    monkeypatch.setattr(scenarios_mod, "load_scenario", lambda name: spec)
    monkeypatch.setattr(scenarios_mod, "resolve_launch_kwargs", lambda p: {"persona": p.persona})

    with pytest.raises(RuntimeError):
        await sp.start(name="demo", browser_pool=pool)
    assert pool.closed == [("a", True)]


@pytest.mark.anyio
async def test_startup_macro_failure_closes_participants(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.macros as macros_mod
    import octowright.scenarios as scenarios_mod
    import octowright.scenarios_pool as pool_mod

    sp = ScenarioPool()
    pool = _Pool()
    spec = _Spec(
        name="demo",
        participants=[_ParticipantSpec("cosmo", "r1"), _ParticipantSpec("ziggy", "r2")],
        fixtures={},
    )

    monkeypatch.setattr(scenarios_mod, "load_scenario", lambda name: spec)
    monkeypatch.setattr(scenarios_mod, "resolve_launch_kwargs", lambda p: {"persona": p.persona})
    monkeypatch.setattr(scenarios_mod, "resolve_startup_macros", lambda p: ["boot"])
    monkeypatch.setattr(pool_mod, "_apply_fixtures", _apply_fixtures)

    async def _raise_macro(session, name, args):
        raise RuntimeError("boom")

    monkeypatch.setattr(macros_mod, "run_macro", _raise_macro)

    with pytest.raises(RuntimeError, match="startup macro failures"):
        await sp.start(name="demo", browser_pool=pool)
    assert pool.closed == [("a", True), ("b", True)]
    assert sp.list_live() == []


@pytest.mark.anyio
async def test_start_cancelled_during_fixtures_completes_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CancelledError during fixture application must still close every launched
    participant — and the rollback must *complete* before the cancellation
    re-propagates (detached create_task closes would leave pool.closed empty)."""
    import asyncio

    import octowright.scenarios as scenarios_mod
    import octowright.scenarios_pool as pool_mod

    sp = ScenarioPool()
    pool = _Pool()
    spec = _Spec(
        name="demo",
        participants=[_ParticipantSpec("cosmo", "r1"), _ParticipantSpec("ziggy", "r2")],
        fixtures={},
    )
    monkeypatch.setattr(scenarios_mod, "load_scenario", lambda name: spec)
    monkeypatch.setattr(scenarios_mod, "resolve_launch_kwargs", lambda p: {"persona": p.persona})

    async def _cancel_fixtures(*_a: Any, **_k: Any) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(pool_mod, "_apply_fixtures", _cancel_fixtures)

    with pytest.raises(asyncio.CancelledError):
        await sp.start(name="demo", browser_pool=pool)
    assert pool.closed == [("a", True), ("b", True)]
    assert sp.list_live() == []


@pytest.mark.anyio
async def test_run_macro_rejects_explicit_role_with_no_matches() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    with pytest.raises(ValueError, match=r"scenario 'sid'.*role 'typo'"):
        await sp.run_macro(scenario_id="sid", macro="m", browser_pool=_Pool(), role="typo")


@pytest.mark.anyio
async def test_run_macro_rejects_explicit_empty_role_with_no_matches() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    with pytest.raises(ValueError, match=r"scenario 'sid'.*role ''"):
        await sp.run_macro(scenario_id="sid", macro="m", browser_pool=_Pool(), role="")


@pytest.mark.anyio
async def test_wait_for_sync_rejects_explicit_role_with_no_matches() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    with pytest.raises(ValueError, match=r"scenario 'sid'.*role 'typo'"):
        await sp.wait_for_sync(scenario_id="sid", browser_pool=_Pool(), role="typo", selector="#x")


@pytest.mark.anyio
async def test_wait_for_sync_rejects_explicit_empty_role_with_no_matches() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    with pytest.raises(ValueError, match=r"scenario 'sid'.*role ''"):
        await sp.wait_for_sync(scenario_id="sid", browser_pool=_Pool(), role="", selector="#x")


@pytest.mark.anyio
async def test_start_requires_name_or_spec() -> None:
    sp = ScenarioPool()
    pool = _Pool()
    with pytest.raises(ValueError):
        await sp.start(name=None, spec=None, browser_pool=pool)
