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
from tests._operation_gate_fakes import OperationAwareFake


@dataclass
class _ParticipantSpec:
    persona: str
    role: str
    kind: str = "chromium"  # real Participant.kind is required; browser by default here


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

    async def set_dialog_policy(self, policy: str):
        self.dialogs.append(policy)


class _Pool:
    def __init__(self) -> None:
        self.sessions = {"a": _Session("a"), "b": _Session("b")}
        self.closed: list[tuple[str, bool]] = []
        self.spawn_error = False

    async def spawn_roster(self, _reqs):
        if self.spawn_error:
            return {"launched": [{"instance_id": "a", "kind": "chromium", "log_path": "a.log"}], "errors": ["boom"]}
        return {
            "launched": [
                {"instance_id": "a", "kind": "chromium", "log_path": "a.log"},
                {"instance_id": "b", "kind": "chromium", "log_path": "b.log"},
            ],
            "errors": [],
        }

    def get(self, instance_id: str):
        return self.sessions[instance_id]

    async def close(self, instance_id: str, *, force: bool = False):
        self.closed.append((instance_id, force))


class _TerminalPool:
    """Minimal plugin-kind pool: close (records) + maybe_get (for remap
    validation). Named for the kind string these tests use ("terminal"), but
    exercises the fully generic plugin-registry routing path -- registered
    below via ``registered_terminal_pool``, exactly as any other plugin kind
    would be."""

    def __init__(self) -> None:
        self.closed: list[tuple[str, bool]] = []
        self._sessions = {"t2": SimpleNamespace(kind="terminal", profile="ops")}

    async def close(self, instance_id: str, *, force: bool = False) -> None:
        self.closed.append((instance_id, force))

    def maybe_get(self, instance_id: str) -> Any:
        return self._sessions.get(instance_id)


class _TerminalDescriptor:
    """Minimal descriptor to register ``_TerminalPool`` under kind "terminal"
    in the plugin registry -- ``create_pool``/``create_scenario_adapter`` are
    never called (the fixture registers the already-built pool directly)."""

    kind = "terminal"
    display_name = "Terminal (test double)"
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


@pytest.fixture
def registered_terminal_pool() -> Any:
    """Register a ``_TerminalPool`` under kind "terminal" for the duration of
    one test, mirroring how a real session-kind plugin (terminal included,
    now that it is one) is registered by the daemon at startup."""
    from octowright.plugins.registry import PluginRegistry
    from octowright.server import plugin_state

    original = plugin_state.registry()
    reg = PluginRegistry()
    tp = _TerminalPool()
    reg.register(_TerminalDescriptor(), pool=tp, adapter=None, discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield tp
    finally:
        plugin_state.set_registry(original)


def _live() -> LiveScenario:
    return LiveScenario(
        scenario_id="sid",
        name="demo",
        spec=_Spec("demo", [_ParticipantSpec("cosmo", "r1")], fixtures={}),
        participants=[{"instance_id": "a", "persona": "cosmo", "role": "r1", "kind": "chromium", "log_path": "a.log"}],
    )


def _mixed_live() -> LiveScenario:
    return LiveScenario(
        scenario_id="mix",
        name="mix",
        spec=_Spec("mix", [], fixtures={}, teardown_macro=None),
        participants=[
            {"instance_id": "b", "persona": "dante", "role": "player", "kind": "chromium", "log_path": "b.log"},
            {
                "instance_id": "t",
                "persona": "ops",
                "role": "operator",
                "kind": "terminal",
                "connector_type": "pty",
                "log_path": "t.log",
            },
        ],
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


# ---------------------------------------------------------------------------
# Terminal participants: close routing + browser-only-op guards + remap routing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stop_routes_terminal_close_to_terminal_pool(registered_terminal_pool: Any) -> None:
    tp = registered_terminal_pool
    sp = ScenarioPool()
    live = _mixed_live()
    sp._live[live.scenario_id] = live
    bp = _Pool()
    summary = await sp.stop(scenario_id="mix", browser_pool=bp)
    assert ("b", True) in bp.closed and tp.closed == [("t", True)]
    assert set(summary["closed"]) == {"b", "t"}


def test_pool_for_a_participant_with_no_recorded_kind_raises() -> None:
    """The missing-kind fallback in _pool_for silently defaulted to the
    browser pool, disagreeing with adapter_for (strict, used for teardown
    macros) about what a kind-less dict is. Every production launch path
    stamps kind, so a dict without one is a bug that must raise, not route
    around."""
    sp = ScenarioPool()
    participant = {"instance_id": "a", "persona": "cosmo", "role": "r1", "log_path": "a.log"}  # no "kind"
    with pytest.raises(ValueError, match="no recorded 'kind'"):
        sp._pool_for(participant, _Pool())


async def test_stop_raises_rather_than_misroute_a_participant_with_no_kind() -> None:
    """stop() must surface the missing-kind bug via its per-participant error
    handling rather than silently closing through the wrong pool."""
    sp = ScenarioPool()
    live = LiveScenario(
        scenario_id="nokind",
        name="nokind",
        spec=_Spec("nokind", [], fixtures={}, teardown_macro=None),
        participants=[{"instance_id": "a", "persona": "cosmo", "role": "r1", "log_path": "a.log"}],
    )
    sp._live[live.scenario_id] = live
    summary = await sp.stop(scenario_id="nokind", browser_pool=_Pool())
    assert summary["closed"] == []
    assert len(summary["teardown_errors"]) == 1
    assert "no recorded 'kind'" in summary["teardown_errors"][0]["error"]


@pytest.mark.anyio
async def test_run_macro_reports_terminal_as_unsupported() -> None:
    sp = ScenarioPool()
    live = _mixed_live()
    sp._live[live.scenario_id] = live
    rm = await sp.run_macro(scenario_id="mix", macro="m", browser_pool=_Pool(), role="operator")
    assert rm["targeted"] == 1
    assert rm["results"][0]["ok"] is False
    assert "does not support macros" in rm["results"][0]["error"], "the error now names the missing capability"


@pytest.mark.anyio
async def test_wait_for_sync_reports_terminal_as_unsupported() -> None:
    sp = ScenarioPool()
    live = _mixed_live()
    sp._live[live.scenario_id] = live
    ws = await sp.wait_for_sync(scenario_id="mix", browser_pool=_Pool(), role="operator", selector="#x")
    assert ws["results"][0]["ok"] is False
    assert "does not support sync" in ws["results"][0]["error"], "the error now names the missing capability"


# ---------------------------------------------------------------------------
# Task 10: the url= branch of wait_for_sync gates per-session, not scenario-wide
# ---------------------------------------------------------------------------


class _UrlGatedSession(OperationAwareFake):
    """Real-gate session fake for the url= branch: ``page.url`` plus a
    ``page.wait_for_url`` that records its calls."""

    def __init__(self, instance_id: str, url: str) -> None:
        self.instance_id = instance_id
        super().__init__()
        self.wait_for_url_calls: list[str] = []
        self.page = SimpleNamespace(url=url, wait_for_url=self._wait_for_url)

    async def _wait_for_url(self, url: str, *, timeout: int) -> None:
        self.wait_for_url_calls.append(url)

    async def wait_for(self, selector=None, text=None, timeout_ms=None):
        return None


class _TwoSessionPool:
    def __init__(self, sessions: dict[str, _UrlGatedSession]) -> None:
        self._sessions = sessions

    def get(self, instance_id: str) -> _UrlGatedSession:
        return self._sessions[instance_id]


@pytest.mark.anyio
async def test_wait_for_sync_url_branch_gates_per_session_not_scenario_wide() -> None:
    """Each participant's url= wait wraps ONLY its own body in
    ``session.operation("scenario_wait_for_sync")`` -- holding one
    participant's gate open under an unrelated operation must not block a
    DIFFERENT participant's wait_for_sync, proving there is no scenario-wide
    lease serializing participants against each other."""
    import asyncio

    session_a = _UrlGatedSession("a", "https://one.test/old")
    session_b = _UrlGatedSession("b", "https://two.test/old")
    pool = _TwoSessionPool({"a": session_a, "b": session_b})

    sp = ScenarioPool()
    live = LiveScenario(
        scenario_id="parallel",
        name="parallel",
        spec=_Spec("parallel", [_ParticipantSpec("cosmo", "r1"), _ParticipantSpec("ziggy", "r2")], fixtures={}),
        participants=[
            {"instance_id": "a", "persona": "cosmo", "role": "r1", "kind": "chromium", "log_path": "a.log"},
            {"instance_id": "b", "persona": "ziggy", "role": "r2", "kind": "chromium", "log_path": "b.log"},
        ],
    )
    sp._live[live.scenario_id] = live

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold_a() -> None:
        async with session_a.operation("unrelated"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold_a())
    await entered.wait()

    sync_task = asyncio.create_task(sp.wait_for_sync(scenario_id="parallel", browser_pool=pool, url="sync-target"))

    async with asyncio.timeout(1):
        while not session_b.wait_for_url_calls:
            await asyncio.sleep(0)

    # "b" completed its url wait while "a" is still blocked holding its own
    # gate -- a scenario-wide lease would have made "b" queue too.
    assert not holder.done()
    assert session_a.wait_for_url_calls == []

    release.set()
    result = await asyncio.wait_for(sync_task, timeout=1.0)
    await holder

    assert {row["instance_id"]: row["ok"] for row in result["results"]} == {"a": True, "b": True}
    assert session_a.wait_for_url_calls == ["sync-target"]
    assert session_b.wait_for_url_calls == ["sync-target"]


def test_remap_terminal_participant_uses_terminal_pool(registered_terminal_pool: Any) -> None:
    sp = ScenarioPool()
    live = _mixed_live()
    sp._live[live.scenario_id] = live
    # registered_terminal_pool: maybe_get("t2") -> terminal session with matching profile
    out = sp.remap_participant(
        scenario_id="mix",
        old_instance_id="t",
        new_instance_id="t2",
        browser_pool=_Pool(),
    )
    assert out["new_instance_id"] == "t2" and out["role"] == "operator"


@pytest.mark.anyio
async def test_stop_completes_teardown_even_when_cancelled() -> None:
    """Cancelling stop() mid-teardown must not strand participants: the scenario
    is already popped from the registry, so an interrupted teardown would leave
    live browsers with no scenario_id to retry. The teardown is shielded, so a
    cancel of the surrounding scope still closes every participant."""
    import anyio

    started = anyio.Event()
    release = anyio.Event()

    class _GatedPool:
        def __init__(self) -> None:
            self.closed: list[str] = []

        async def close(self, instance_id: str, *, force: bool = False) -> None:
            if instance_id == "b":
                started.set()
                await release.wait()
            self.closed.append(instance_id)

    sp = ScenarioPool()
    live = LiveScenario(
        scenario_id="cx",
        name="cx",
        spec=_Spec("cx", [], fixtures={}, teardown_macro=None),
        participants=[
            {"instance_id": "b", "persona": "d", "role": "player", "kind": "chromium", "log_path": "b.log"},
            {"instance_id": "c", "persona": "d", "role": "player", "kind": "chromium", "log_path": "c.log"},
        ],
    )
    sp._live[live.scenario_id] = live
    pool = _GatedPool()

    async def _run() -> None:
        await sp.stop(scenario_id="cx", browser_pool=pool)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_run)
        await started.wait()
        tg.cancel_scope.cancel()  # cancel the scope while closing participant "b"
        release.set()

    assert pool.closed == ["b", "c"]
