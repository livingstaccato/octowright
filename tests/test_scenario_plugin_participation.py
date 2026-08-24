# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins.registry import PluginRegistry
from octowright.scenarios import Participant, Scenario
from octowright.scenarios_pool import ScenarioPool
from octowright.server import plugin_state


class _RefSession:
    def __init__(self, instance_id: str, persona: str | None) -> None:
        self.instance_id = instance_id
        self.kind = "refkind"
        self.profile = persona


class _RefPool:
    """Minimal pool: launch records what it was asked for and hands back an id."""

    def __init__(self) -> None:
        self.launched: list[dict[str, Any]] = []
        self.closed: list[str] = []
        self._n = 0

    async def launch(self, **kwargs: Any) -> dict[str, Any]:
        self._n += 1
        instance_id = f"ref{self._n:09d}"
        self.launched.append(kwargs)
        return {"instance_id": instance_id, "kind": "refkind", "label": kwargs.get("label")}

    async def close(self, instance_id: str, *, force: bool = False) -> dict[str, Any]:
        self.closed.append(instance_id)
        return {"instance_id": instance_id, "closed": True}


class _RefAdapter:
    def __init__(self, pool: _RefPool) -> None:
        self._pool = pool

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        return {"label": spec.persona, "profile": spec.persona}


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
        return _RefAdapter(pool)

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _BrowserPool:
    async def spawn_roster(self, roster: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        # The real spawn_roster returns {"launched": [...], "errors": [...]},
        # and _launch_participants indexes both. A fake returning a bare list
        # would make these tests pass against a contract that does not exist.
        return {
            "launched": [
                {"instance_id": f"br{i:010d}", "kind": r.get("kind", "chromium")} for i, r in enumerate(roster)
            ],
            "errors": [],
        }

    async def close(self, instance_id: str, *, force: bool = False) -> None:
        return None


@pytest.fixture
def registered():
    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _RefPool()
    reg.register(_Descriptor(), pool=pool, adapter=_RefAdapter(pool), discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg, pool
    finally:
        plugin_state.set_registry(original)


async def test_a_plugin_participant_launches_through_its_own_pool(registered):
    _, ref_pool = registered
    spec = Scenario(
        name="mixed",
        participants=[
            Participant(persona="tanuki-tim", kind="chromium", role="player"),
            Participant(persona="ref-rita", kind="refkind", role="monitor"),
        ],
    )
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)

    assert len(live.participants) == 2
    assert live.participants[0]["kind"] == "chromium"
    assert live.participants[1]["kind"] == "refkind"
    assert ref_pool.launched == [{"label": "ref-rita", "profile": "ref-rita"}]


async def test_participants_reassemble_in_declaration_order(registered):
    """Grouping by kind must not reorder the roster -- roles line up by index."""
    spec = Scenario(
        name="interleaved",
        participants=[
            Participant(persona="a", kind="refkind", role="monitor"),
            Participant(persona="b", kind="chromium", role="player"),
            Participant(persona="c", kind="refkind", role="spectator"),
        ],
    )
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    assert [p["persona"] for p in live.participants] == ["a", "b", "c"]
    assert [p["role"] for p in live.participants] == ["monitor", "player", "spectator"]
    assert [p["kind"] for p in live.participants] == ["refkind", "chromium", "refkind"]


async def test_a_scenario_naming_an_unregistered_kind_fails_before_launching_anything(registered):
    _, ref_pool = registered
    spec = Scenario(name="bad", participants=[Participant(persona="x", kind="nosuchkind", role="player")])
    sp = ScenarioPool()
    with pytest.raises((RuntimeError, KeyError, ValueError)):
        await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    assert ref_pool.launched == [], "nothing may launch for an unresolvable roster"


class _FailingBrowserPool:
    """A browser pool whose roster always reports an error -- for pinning
    that a failed roster stops the plugin group before it launches anything."""

    async def spawn_roster(self, roster: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        return {"launched": [], "errors": ["boom"]}

    async def close(self, instance_id: str, *, force: bool = False) -> None:
        return None


async def test_a_failed_browser_roster_prevents_any_plugin_launch(registered):
    """Mirrors _launch_terminals' own errors-so-far early-out: a roster that
    already failed must not go on to open a plugin session either. Assert the
    plugin pool's own launched list, not just that the scenario failed --
    that is the only way to prove the plugin launch was never attempted."""
    _, ref_pool = registered
    spec = Scenario(
        name="mixed",
        participants=[
            Participant(persona="tanuki-tim", kind="chromium", role="player"),
            Participant(persona="ref-rita", kind="refkind", role="monitor"),
        ],
    )
    sp = ScenarioPool()
    with pytest.raises(RuntimeError):
        await sp.start(spec=spec, browser_pool=_FailingBrowserPool(), terminal_pool=None)
    assert ref_pool.launched == [], "a failed browser roster must not open a plugin session"


class _MacroRefAdapter(_RefAdapter):
    def __init__(self, pool: _RefPool) -> None:
        super().__init__(pool)
        self.ran: list[tuple[str, str, dict[str, Any]]] = []

    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
        self.ran.append((instance_id, name, args))


@pytest.fixture
def registered_with_macros():
    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _RefPool()
    adapter = _MacroRefAdapter(pool)
    reg.register(_Descriptor(), pool=pool, adapter=adapter, discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg, pool, adapter
    finally:
        plugin_state.set_registry(original)


async def test_run_macro_reports_a_kind_without_the_capability(registered):
    spec = Scenario(name="s", participants=[Participant(persona="a", kind="refkind", role="monitor")])
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    result = await sp.run_macro(scenario_id=live.scenario_id, macro="login", browser_pool=_BrowserPool())
    outcome = result["results"][0]
    assert outcome["ok"] is False
    assert "macros" in outcome["error"], "the error must name the missing capability, not the kind"


async def test_run_macro_dispatches_to_an_adapter_that_has_the_capability(registered_with_macros):
    _, _, adapter = registered_with_macros
    spec = Scenario(name="s", participants=[Participant(persona="a", kind="refkind", role="monitor")])
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    result = await sp.run_macro(
        scenario_id=live.scenario_id, macro="login", browser_pool=_BrowserPool(), args={"u": "x"}
    )
    assert result["results"][0]["ok"] is True
    assert adapter.ran == [(live.participants[0]["instance_id"], "login", {"u": "x"})]


async def test_startup_macros_run_through_the_adapter(registered_with_macros):
    _, _, adapter = registered_with_macros
    spec = Scenario(
        name="s",
        participants=[Participant(persona="a", kind="refkind", role="monitor", startup_macros=["boot"])],
    )
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    assert [name for _id, name, _args in adapter.ran] == ["boot"]
    assert live.participants[0]["kind"] == "refkind"
