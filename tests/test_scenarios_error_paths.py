# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Error-path coverage for ScenarioPool.

These tests exercise rollback, validation, and teardown error handling that the
existing happy-path test in ``test_scenarios_pool.py`` does not cover:

* spawn_roster returning errors -> rollback of the launched subset
* close() raising during rollback -> original launch error still propagates
* startup macro failure -> full rollback after a successful spawn
* remap_participant -> browser_pool kwarg required, kind mismatch rejected
* stop() -> every participant gets close() attempted even if one raises
* get() -> missing-id KeyError carries an actionable hint when none are live
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.scenarios_pool import LiveScenario, ScenarioPool


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


class _FakePool:
    """Minimal browser_pool stub for ScenarioPool start/stop/remap paths.

    Each instance owns its own ``launched``/``errors`` programmed reply for
    ``spawn_roster``, and tracks every ``close`` invocation so tests can assert
    the rollback fan-out. ``close_failures`` is a set of instance ids whose
    ``close`` should raise — modeling the "rollback close itself fails" path
    that must not mask the original error.
    """

    def __init__(
        self,
        *,
        launched: list[dict[str, Any]] | None = None,
        errors: list[Any] | None = None,
        close_failures: set[str] | None = None,
    ) -> None:
        self._launched = launched or []
        self._errors = errors or []
        self._close_failures = close_failures or set()
        self.close_calls: list[tuple[str, bool]] = []
        self.spawn_calls: list[list[dict[str, Any]]] = []
        self._sessions: dict[str, Any] = {}

    async def spawn_roster(self, reqs: list[dict[str, Any]]) -> dict[str, Any]:
        self.spawn_calls.append(reqs)
        return {"launched": list(self._launched), "errors": list(self._errors)}

    def get(self, instance_id: str) -> Any:
        if instance_id not in self._sessions:
            self._sessions[instance_id] = SimpleNamespace(
                instance_id=instance_id,
                set_dialog_policy=lambda _p: None,
                mock_route=_noop_mock_route,
            )
        return self._sessions[instance_id]

    def maybe_get(self, instance_id: str) -> Any | None:
        return self._sessions.get(instance_id)

    async def close(self, instance_id: str, *, force: bool = False) -> None:
        self.close_calls.append((instance_id, force))
        if instance_id in self._close_failures:
            raise RuntimeError(f"close failed for {instance_id}")


async def _noop_mock_route(*_a: Any, **_kw: Any) -> None:
    return None


def _stub_scenarios_module(monkeypatch: pytest.MonkeyPatch, spec: _Spec) -> None:
    import octowright.scenarios as scenarios_mod

    monkeypatch.setattr(scenarios_mod, "load_scenario", lambda name: spec)
    monkeypatch.setattr(scenarios_mod, "resolve_launch_kwargs", lambda p: {"persona": p.persona})
    monkeypatch.setattr(scenarios_mod, "resolve_startup_macros", lambda _p: [])


@pytest.mark.anyio
async def test_start_rolls_back_launches_when_spawn_roster_returns_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spawn_roster returns mixed launched+errors → launched browsers are closed,
    a descriptive RuntimeError propagates, and ``_live`` ends empty."""
    spec = _Spec(
        name="three-up",
        participants=[
            _ParticipantSpec("cosmo", "r1"),
            _ParticipantSpec("ziggy", "r2"),
            _ParticipantSpec("nova", "r3"),
        ],
        fixtures={},
    )
    _stub_scenarios_module(monkeypatch, spec)

    pool = _FakePool(
        launched=[
            {"instance_id": "i-a", "log_path": "a.log"},
            {"instance_id": "i-b", "log_path": "b.log"},
        ],
        errors=[{"spec": {"persona": "nova"}, "error": "boom"}],
    )
    sp = ScenarioPool()

    with pytest.raises(RuntimeError, match="three-up") as excinfo:
        await sp.start(name="three-up", browser_pool=pool)
    # Error message references the failing-participant payload
    assert "boom" in str(excinfo.value)
    # Both successfully-launched browsers were closed during rollback
    assert sorted(pool.close_calls) == [("i-a", True), ("i-b", True)]
    # No live scenarios remain
    assert sp.list_live() == []


@pytest.mark.anyio
async def test_start_propagates_close_failure_during_rollback_without_swallowing_original(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If close() raises while rolling back, the rollback continues for the
    other browsers, the failure is logged at WARNING, and the original
    RuntimeError (naming the launch error) is what propagates."""
    spec = _Spec(
        name="rb",
        participants=[
            _ParticipantSpec("cosmo", "r1"),
            _ParticipantSpec("ziggy", "r2"),
            _ParticipantSpec("nova", "r3"),
        ],
        fixtures={},
    )
    _stub_scenarios_module(monkeypatch, spec)

    pool = _FakePool(
        launched=[
            {"instance_id": "i-a", "log_path": "a.log"},
            {"instance_id": "i-b", "log_path": "b.log"},
        ],
        errors=[{"spec": {"persona": "nova"}, "error": "spawn-broke"}],
        close_failures={"i-a"},
    )
    sp = ScenarioPool()

    with (
        caplog.at_level(logging.WARNING, logger="octowright.scenarios_pool"),
        pytest.raises(RuntimeError, match="spawn-broke"),
    ):
        await sp.start(name="rb", browser_pool=pool)
    # Both close()s were attempted (failure on i-a didn't short-circuit i-b)
    assert sorted(pool.close_calls) == [("i-a", True), ("i-b", True)]
    # Rollback close failure was logged via the structured event key
    assert any("scenario.rollback.close_failed" in rec.message for rec in caplog.records)
    assert sp.list_live() == []


@pytest.mark.anyio
async def test_startup_macros_failure_triggers_full_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spawn_roster succeeds; ``_run_startup_macros`` raises → every launched
    browser is closed, the scenario never lands in ``_live``, and the error
    propagates."""
    spec = _Spec(
        name="sm",
        participants=[_ParticipantSpec("cosmo", "r1"), _ParticipantSpec("ziggy", "r2")],
        fixtures={},
    )
    _stub_scenarios_module(monkeypatch, spec)

    pool = _FakePool(
        launched=[
            {"instance_id": "i-a", "log_path": "a.log"},
            {"instance_id": "i-b", "log_path": "b.log"},
        ],
        errors=[],
    )
    sp = ScenarioPool()

    import octowright.scenarios_pool as pool_mod

    async def _explode(_browser_pool: Any, _live: LiveScenario) -> None:
        raise RuntimeError("startup-macro-exploded")

    monkeypatch.setattr(pool_mod, "_run_startup_macros", _explode)

    with pytest.raises(RuntimeError, match="startup-macro-exploded"):
        await sp.start(name="sm", browser_pool=pool)
    assert sorted(pool.close_calls) == [("i-a", True), ("i-b", True)]
    assert sp.list_live() == []
    assert sp.maybe_get("sm") is None


def _seed_live(sp: ScenarioPool, *, kind: str = "chromium") -> LiveScenario:
    live = LiveScenario(
        scenario_id="sid-err",
        name="demo",
        spec=_Spec("demo", [_ParticipantSpec("cosmo", "r1")], fixtures={}),
        participants=[
            {
                "instance_id": "old-iid",
                "persona": "cosmo",
                "role": "r1",
                "kind": kind,
                "log_path": "x.log",
            }
        ],
    )
    sp._live[live.scenario_id] = live
    return live


def test_remap_participant_requires_browser_pool_kwarg() -> None:
    """remap_participant must reject calls without browser_pool, then accept
    the same call when one is supplied (post-A2-refactor contract)."""
    sp = ScenarioPool()
    _seed_live(sp)

    with pytest.raises(ValueError, match="browser_pool is required for remap validation"):
        sp.remap_participant(
            scenario_id="sid-err",
            old_instance_id="old-iid",
            new_instance_id="new-iid",
        )

    bp = SimpleNamespace(
        maybe_get=lambda iid: SimpleNamespace(kind="chromium", profile="cosmo") if iid == "new-iid" else None
    )
    out = sp.remap_participant(
        scenario_id="sid-err",
        old_instance_id="old-iid",
        new_instance_id="new-iid",
        browser_pool=bp,
    )
    assert out["new_instance_id"] == "new-iid"
    assert sp.get("sid-err").participants[0]["instance_id"] == "new-iid"


def test_remap_rejects_kind_mismatch() -> None:
    """Replacement with a different engine kind raises ValueError naming both
    kinds; the live participant binding is left untouched."""
    sp = ScenarioPool()
    _seed_live(sp, kind="chromium")

    bp = SimpleNamespace(
        maybe_get=lambda iid: SimpleNamespace(kind="firefox", profile="cosmo") if iid == "new-iid" else None
    )

    with pytest.raises(ValueError) as excinfo:
        sp.remap_participant(
            scenario_id="sid-err",
            old_instance_id="old-iid",
            new_instance_id="new-iid",
            browser_pool=bp,
        )
    msg = str(excinfo.value)
    assert "firefox" in msg
    assert "chromium" in msg
    # Original binding unchanged
    assert sp.get("sid-err").participants[0]["instance_id"] == "old-iid"


@pytest.mark.anyio
async def test_stop_closes_every_participant_even_when_one_close_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """stop() must fan close() across every participant even if a middle one
    raises, record the failing instance id in ``teardown_errors``, and remove
    the scenario from ``_live``."""
    sp = ScenarioPool()
    live = LiveScenario(
        scenario_id="sid-stop",
        name="demo",
        spec=_Spec("demo", [], fixtures={}, teardown_macro=None),
        participants=[
            {"instance_id": "i-a", "persona": "cosmo", "role": "r1", "log_path": "a.log"},
            {"instance_id": "i-b", "persona": "ziggy", "role": "r2", "log_path": "b.log"},
            {"instance_id": "i-c", "persona": "nova", "role": "r3", "log_path": "c.log"},
        ],
    )
    sp._live[live.scenario_id] = live

    pool = _FakePool(close_failures={"i-b"})

    with caplog.at_level(logging.WARNING, logger="octowright.scenarios_pool"):
        summary = await sp.stop(scenario_id="sid-stop", browser_pool=pool)

    # close() was attempted on every participant despite the middle one raising
    assert pool.close_calls == [("i-a", True), ("i-b", True), ("i-c", True)]
    # Closed list excludes the failing one
    assert set(summary["closed"]) == {"i-a", "i-c"}
    # Teardown error captured the failing instance
    assert any(err["instance_id"] == "i-b" for err in summary["teardown_errors"])
    # Scenario removed from live registry
    assert sp.maybe_get("sid-stop") is None


def test_get_unknown_scenario_id_includes_hint_when_none_live() -> None:
    """get() on an empty pool surfaces the actionable startup hint."""
    sp = ScenarioPool()
    with pytest.raises(KeyError) as excinfo:
        sp.get("missing-id")
    # KeyError stringifies its argument with repr; check the inner message instead.
    msg = excinfo.value.args[0]
    assert "no scenarios are running" in msg
    assert "scenario_start" in msg
    assert "missing-id" in msg
