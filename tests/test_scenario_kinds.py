# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins.registry import PluginRegistry
from octowright.scenario_kinds import (
    adapter_for,
    capabilities_for,
    known_kinds,
    pool_for_kind,
    supports,
)
from octowright.server import plugin_state


class _RefAdapter:
    """A partial adapter: the floor and nothing else."""

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        return {"label": spec.persona}


class _MacroAdapter(_RefAdapter):
    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
        return None


class _Descriptor:
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None
    frontend = None

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> Any:
        raise AssertionError("not used")

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


@pytest.fixture
def registered():
    original = plugin_state.registry()
    reg = PluginRegistry()
    reg.register(_Descriptor("refkind"), pool="REFPOOL", adapter=_RefAdapter(), discovered=None)
    reg.register(_Descriptor("macrokind"), pool="MACROPOOL", adapter=_MacroAdapter(), discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg
    finally:
        plugin_state.set_registry(original)


@pytest.fixture
def registered_with_adapterless():
    """Includes a plugin whose ``create_scenario_adapter`` returned ``None`` --
    the case spec 7.1 says must NOT count as a valid participant kind."""
    original = plugin_state.registry()
    reg = PluginRegistry()
    reg.register(_Descriptor("refkind"), pool="REFPOOL", adapter=_RefAdapter(), discovered=None)
    reg.register(_Descriptor("noadapterkind"), pool="NOADAPTERPOOL", adapter=None, discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg
    finally:
        plugin_state.set_registry(original)


def test_browser_kinds_resolve_to_the_browser_adapter():
    from octowright.scenario_adapters import BrowserScenarioAdapter

    ad = adapter_for("chromium", browser_pool="BROWSERPOOL")
    assert isinstance(ad, BrowserScenarioAdapter)


def test_a_plugin_kind_resolves_to_its_registered_adapter(registered):
    ad = adapter_for("refkind", browser_pool="BROWSERPOOL")
    assert isinstance(ad, _RefAdapter)


def test_an_unknown_kind_resolves_to_none(registered):
    assert adapter_for("nosuchkind", browser_pool="BROWSERPOOL") is None


def test_browser_capabilities_are_all_four():
    assert capabilities_for("chromium", browser_pool="BROWSERPOOL") == {
        "macros",
        "sync",
        "dialog_policy",
        "mock_routes",
    }


def test_a_partial_adapter_reports_no_capabilities(registered):
    assert capabilities_for("refkind", browser_pool="BROWSERPOOL") == frozenset()
    assert supports("refkind", "macros", browser_pool="BROWSERPOOL") is False


def test_capabilities_are_derived_from_what_the_adapter_implements(registered):
    assert capabilities_for("macrokind", browser_pool="BROWSERPOOL") == {"macros"}
    assert supports("macrokind", "macros", browser_pool="BROWSERPOOL") is True
    assert supports("macrokind", "sync", browser_pool="BROWSERPOOL") is False


def test_pool_for_kind_routes_by_kind(registered):
    assert pool_for_kind("chromium", browser_pool="BROWSERPOOL") == "BROWSERPOOL"
    assert pool_for_kind("refkind", browser_pool="BROWSERPOOL") == "REFPOOL"


def test_pool_for_an_unknown_kind_raises(registered):
    with pytest.raises(KeyError, match="nosuchkind"):
        pool_for_kind("nosuchkind", browser_pool="B")


def test_known_kinds_lists_browsers_and_plugins(registered):
    kinds = known_kinds()
    assert "chromium" in kinds
    assert "refkind" in kinds
    assert kinds == sorted(kinds), "sorted so an error message is stable"


def test_known_kinds_excludes_a_plugin_with_no_scenario_adapter(registered_with_adapterless):
    """Spec 7.1: a valid participant kind is one registered by an enabled
    plugin whose create_scenario_adapter returned an adapter. A plugin
    registered with adapter=None must not pass kind validation only to fail
    later at launch."""
    reg = registered_with_adapterless
    assert reg.get_plugin("noadapterkind").adapter is None, "the plugin is loaded, just capability-less"
    kinds = known_kinds()
    assert "refkind" in kinds
    assert "noadapterkind" not in kinds


def test_an_adapterless_plugin_kind_is_refused_at_participant_validation(registered_with_adapterless):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    reg = registered_with_adapterless
    assert reg.get_plugin("noadapterkind").adapter is None
    p = Participant(persona="tanuki-tim", kind="noadapterkind", role="player")
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError, match="noadapterkind"):
        _validate_participant_kind(s, p)


def test_a_registered_plugin_kind_validates(registered):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    p = Participant(persona="tanuki-tim", kind="refkind", role="player")
    s = Scenario(name="demo", participants=[p])
    _validate_participant_kind(s, p)  # must not raise


def test_an_unregistered_kind_is_refused_and_the_error_lists_what_is_available(registered):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    p = Participant(persona="tanuki-tim", kind="notaplugin", role="player")
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError) as excinfo:
        _validate_participant_kind(s, p)
    message = str(excinfo.value)
    assert "notaplugin" in message
    assert "refkind" in message, "a disabled or mistyped plugin must be self-diagnosing"
    assert "chromium" in message


def test_a_kind_without_macros_cannot_declare_startup_macros(registered):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    p = Participant(persona="tanuki-tim", kind="refkind", role="player", startup_macros=["login"])
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError, match="startup_macros"):
        _validate_participant_kind(s, p)


def test_a_kind_with_macros_may_declare_startup_macros(registered):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    p = Participant(persona="tanuki-tim", kind="macrokind", role="player", startup_macros=["login"])
    s = Scenario(name="demo", participants=[p])
    _validate_participant_kind(s, p)  # must not raise
