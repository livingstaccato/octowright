# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys
import types
from typing import Any

from octowright.plugins.contract import PLUGIN_API_VERSION
from octowright.plugins.discovery import DiscoveredPlugin
from octowright.plugins.loader import activate, resolve_descriptors
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext


class _FakeToolManager:
    def __init__(self) -> None:
        self._tools: dict[str, object] = {"browser_launch": object()}


class _Descriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = PLUGIN_API_VERSION
    tool_names = frozenset({"refkind_launch"})
    tool_module = None
    profile_name = None
    frontend = None

    def __init__(self, **overrides: Any) -> None:
        for key, value in overrides.items():
            setattr(self, key, value)

    def create_pool(self, ctx: Any) -> Any:
        return _Pool()

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _Pool:
    def maybe_get(self, instance_id: str) -> None:
        return None

    def iter_sessions(self):
        return iter(())

    async def close_all(self, *, force: bool = False) -> None:
        return None


class _TrackingPool:
    """A pool whose ``close_all`` records whether it was actually awaited."""

    def __init__(self) -> None:
        self.closed = False

    def maybe_get(self, instance_id: str) -> None:
        return None

    def iter_sessions(self):
        return iter(())

    async def close_all(self, *, force: bool = False) -> None:
        self.closed = True


class _FakeEP:
    name = "refkind"
    value = "m:p"

    def __init__(self, target: Any = None, raises: BaseException | None = None) -> None:
        self._target = target
        self._raises = raises

    def load(self) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._target


def _ctx_factory(kind: str, registry: PluginRegistry, tmp_path) -> PluginContext:
    return PluginContext(kind=kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)


def test_disabled_plugin_is_never_loaded():
    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor())
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)

    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=[])

    assert resolved == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "disabled"
    assert "kind" not in rows["refkind"]


def test_enabled_name_with_no_entry_point_reports_missing():
    reg = PluginRegistry()
    resolve_descriptors(registry=reg, discovered=[], enabled=["typo"])
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["typo"]["state"] == "missing"


def test_api_version_mismatch_is_refused():
    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(plugin_api_version=PLUGIN_API_VERSION + 1))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)

    assert resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"]) == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "failed"
    assert "plugin_api_version" in rows["refkind"]["reason"]


def test_descriptor_import_failure_reports_without_descriptor_fields():
    reg = PluginRegistry()
    ep = _FakeEP(raises=RuntimeError("bad import"))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)

    assert resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"]) == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "failed"
    assert "bad import" in rows["refkind"]["reason"]
    assert "kind" not in rows["refkind"]


def test_reserved_kind_is_refused():
    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(kind="browser", tool_names=frozenset({"browser_launch"})))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)

    assert resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"]) == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert "reserved" in rows["refkind"]["reason"]


def test_tool_name_colliding_with_core_is_refused_before_import(tmp_path):
    # `macro` is NOT a reserved kind, so this reaches the collision check
    # rather than short-circuiting on validate_kind. Using a reserved kind here
    # would make the test pass for the wrong reason.
    manager = _FakeToolManager()
    manager._tools["macro_run"] = object()

    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(kind="macro", tool_names=frozenset({"macro_run"})))
    found = DiscoveredPlugin(name="rogue", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["rogue"])

    imported: list[str] = []
    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
        import_module=imported.append,
    )

    # Refused BEFORE the tool module was imported, and core's tool survives.
    assert imported == []
    assert reg.kinds() == []
    assert manager._tools["macro_run"] is not None
    rows = {row["name"]: row for row in reg.status_rows()}
    assert "collision" in rows["rogue"]["reason"]


def test_two_plugins_claiming_one_tool_name_collide(tmp_path):
    manager = _FakeToolManager()
    reg = PluginRegistry()
    first = DiscoveredPlugin(
        name="a",
        distribution="d",
        version="1",
        entry_point="m:p",
        ep=_FakeEP(target=_Descriptor(kind="alpha", tool_names=frozenset({"alpha_go"}))),
    )
    # A second plugin whose kind differs but which declares the same tool name
    # is impossible under the prefix rule, so the cross-plugin guard is checked
    # by giving both the same kind — two distributions, one kind.
    second = DiscoveredPlugin(
        name="b",
        distribution="d",
        version="1",
        entry_point="m:p",
        ep=_FakeEP(target=_Descriptor(kind="alpha", tool_names=frozenset({"alpha_go"}))),
    )
    resolved = resolve_descriptors(registry=reg, discovered=[first, second], enabled=["a", "b"], tool_manager=manager)

    assert [item.discovered.name for item in resolved] == ["a"]
    rows = {row["name"]: row for row in reg.status_rows()}
    assert "collision" in rows["b"]["reason"]


def test_activate_registers_the_pool(tmp_path):
    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor())
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=_FakeToolManager(),
    )

    assert reg.kinds() == ["refkind"]


def test_rollback_removes_the_actual_delta_not_the_declaration(tmp_path):
    # A module that registers an UNDECLARED tool and then raises: rolling back
    # by declared name alone would leak it.
    manager = _FakeToolManager()
    module_name = "octowright_test_rogue_tool_module"

    def _install() -> None:
        module = types.ModuleType(module_name)

        def _side_effect() -> None:
            manager._tools["refkind_launch"] = object()
            manager._tools["refkind_undeclared"] = object()
            raise RuntimeError("registered then died")

        module.__dict__["_boot"] = _side_effect
        sys.modules[module_name] = module
        _side_effect()

    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(tool_module=module_name))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
        import_module=lambda name: _install(),
    )

    assert "refkind_launch" not in manager._tools
    assert "refkind_undeclared" not in manager._tools
    assert manager._tools.keys() == {"browser_launch"}
    assert reg.kinds() == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "failed"


def test_activation_failure_after_pool_created_closes_the_pool(tmp_path):
    # create_pool SUCCEEDS, then create_scenario_adapter raises: the pool
    # exists and owns resources (SessionPool.close_all is how it releases
    # them), so rollback must await close_all rather than dropping it.
    manager = _FakeToolManager()
    module_name = "octowright_test_pool_leak_module"
    created_pools: list[_TrackingPool] = []

    class _AdapterFailsDescriptor(_Descriptor):
        tool_module = module_name

        def create_pool(self, ctx: Any) -> Any:
            pool = _TrackingPool()
            created_pools.append(pool)
            return pool

        def create_scenario_adapter(self, pool: Any) -> Any:
            raise RuntimeError("adapter refused")

    def _register(name: str) -> None:
        manager._tools["refkind_launch"] = object()

    reg = PluginRegistry()
    ep = _FakeEP(target=_AdapterFailsDescriptor())
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
        import_module=_register,
    )

    assert len(created_pools) == 1
    assert created_pools[0].closed is True
    assert manager._tools.keys() == {"browser_launch"}
    assert reg.kinds() == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "failed"


def test_create_pool_failure_rolls_back_tools(tmp_path):
    manager = _FakeToolManager()

    class _BadPoolDescriptor(_Descriptor):
        def create_pool(self, ctx: Any) -> Any:
            raise RuntimeError("pool refused")

    reg = PluginRegistry()
    ep = _FakeEP(target=_BadPoolDescriptor(tool_module="whatever"))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    def _register(name: str) -> None:
        manager._tools["refkind_launch"] = object()

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
        import_module=_register,
    )

    assert manager._tools.keys() == {"browser_launch"}
    assert reg.kinds() == []


def test_two_plugins_claiming_one_kind_collide(tmp_path):
    # Distinct tool names, so nothing but the KIND check can refuse this. The
    # registry is keyed by kind: a second claimant would silently replace the
    # first's pool (dropped without close_all) while both reported `enabled`,
    # and the first's already-registered tools would resolve through
    # pool_for(kind) to the second plugin's pool.
    manager = _FakeToolManager()
    reg = PluginRegistry()
    first = DiscoveredPlugin(
        name="a",
        distribution="d",
        version="1",
        entry_point="m:p",
        ep=_FakeEP(target=_Descriptor(kind="alpha", tool_names=frozenset({"alpha_go"}))),
    )
    second = DiscoveredPlugin(
        name="b",
        distribution="d",
        version="1",
        entry_point="m:p",
        ep=_FakeEP(target=_Descriptor(kind="alpha", tool_names=frozenset({"alpha_stop"}))),
    )

    resolved = resolve_descriptors(registry=reg, discovered=[first, second], enabled=["a", "b"], tool_manager=manager)

    assert [item.discovered.name for item in resolved] == ["a"]
    rows = {row["name"]: row for row in reg.status_rows()}
    assert "kind collision" in rows["b"]["reason"]

    # The first plugin still activates and owns the kind.
    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
    )
    assert reg.kinds() == ["alpha"]


def test_a_failed_kind_claim_does_not_poison_a_later_distinct_kind(tmp_path):
    # claimed_kinds must accumulate only for descriptors that PASSED, exactly
    # like the tool-name `claimed` set.
    reg = PluginRegistry()
    bad = DiscoveredPlugin(
        name="bad",
        distribution="d",
        version="1",
        entry_point="m:p",
        ep=_FakeEP(target=_Descriptor(kind="alpha", plugin_api_version=PLUGIN_API_VERSION + 1)),
    )
    good = DiscoveredPlugin(
        name="good",
        distribution="d",
        version="1",
        entry_point="m:p",
        ep=_FakeEP(target=_Descriptor(kind="alpha", tool_names=frozenset({"alpha_go"}))),
    )

    resolved = resolve_descriptors(registry=reg, discovered=[bad, good], enabled=["bad", "good"])

    assert [item.discovered.name for item in resolved] == ["good"]


def test_a_duplicate_entry_point_name_does_not_erase_other_plugins(tmp_path):
    manager = _FakeToolManager()
    reg = PluginRegistry()
    dupe = DiscoveredPlugin(
        name="dupe",
        distribution="d",
        version="1",
        entry_point="m:p",
        ep=_FakeEP(target=_Descriptor()),
        conflict="two distributions declare the octowright.session_kinds entry point 'dupe'",
    )
    good = DiscoveredPlugin(
        name="refkind",
        distribution="d",
        version="1",
        entry_point="m:p",
        ep=_FakeEP(target=_Descriptor()),
    )

    resolved = resolve_descriptors(registry=reg, discovered=[dupe, good], enabled=["dupe", "refkind"])
    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
    )

    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["dupe"]["state"] == "failed"
    assert "two distributions" in rows["dupe"]["reason"]
    # The correctly configured neighbour still loads and is still reported.
    assert rows["refkind"]["state"] == "enabled"
    assert reg.kinds() == ["refkind"]


def test_an_undeclared_registration_refuses_the_plugin(tmp_path):
    # The declaration is what the collision check reasoned about, so a tool
    # outside tool_names went through no collision check at all.
    manager = _FakeToolManager()

    def _register(name: str) -> None:
        manager._tools["refkind_launch"] = object()
        manager._tools["refkind_lunch"] = object()

    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(tool_module="whatever"))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
        import_module=_register,
    )

    assert manager._tools.keys() == {"browser_launch"}
    assert reg.kinds() == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert "undeclared" in rows["refkind"]["reason"]


def test_a_delta_narrower_than_the_declaration_is_allowed(tmp_path):
    # A narrow OCTOWRIGHT_PROFILE legitimately suppresses declared tools, so a
    # SMALLER delta must not fail the plugin.
    manager = _FakeToolManager()

    def _register(name: str) -> None:
        manager._tools["refkind_launch"] = object()

    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(tool_module="whatever", tool_names=frozenset({"refkind_launch", "refkind_close"})))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
        import_module=_register,
    )

    assert reg.kinds() == ["refkind"]
    assert "refkind_launch" in manager._tools


# ---------------------------------------------------------------------------
# scenario-adapter contract check at load time
# ---------------------------------------------------------------------------


class _ValidAdapter:
    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        return {}


class _SyncRunMacroAdapter(_ValidAdapter):
    """Satisfies `isinstance(..., SupportsMacros)` but core awaits `run_macro`."""

    def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
        return None


def _activate_with_adapter(adapter: Any, tmp_path, pools: list[_TrackingPool]) -> PluginRegistry:
    class _AdapterDescriptor(_Descriptor):
        def create_pool(self, ctx: Any) -> Any:
            pool = _TrackingPool()
            pools.append(pool)
            return pool

        def create_scenario_adapter(self, pool: Any) -> Any:
            return adapter

    reg = PluginRegistry()
    ep = _FakeEP(target=_AdapterDescriptor())
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])
    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=_FakeToolManager(),
    )
    return reg


def test_adapter_with_a_sync_capability_method_is_refused(tmp_path):
    # The gap this closes: `isinstance` against a runtime_checkable Protocol
    # tests attribute presence only, so this adapter is reported as supporting
    # `macros` and blows up with a TypeError mid-scenario, after browsers have
    # launched -- read as a scenario failure rather than the plugin defect it is.
    pools: list[_TrackingPool] = []
    reg = _activate_with_adapter(_SyncRunMacroAdapter(), tmp_path, pools)

    assert reg.kinds() == []
    row = next(r for r in reg.status_rows() if r["name"] == "refkind")
    assert row["state"] == "failed"
    assert "run_macro" in row["reason"]
    # Rollback still runs: the pool was built before the check.
    assert pools and pools[0].closed


def test_adapter_missing_the_mandatory_floor_is_refused(tmp_path):
    class _NoFloor:
        pass

    pools: list[_TrackingPool] = []
    reg = _activate_with_adapter(_NoFloor(), tmp_path, pools)

    assert reg.kinds() == []
    row = next(r for r in reg.status_rows() if r["name"] == "refkind")
    assert "resolve_participant" in row["reason"]


def test_valid_adapter_still_loads(tmp_path):
    pools: list[_TrackingPool] = []
    reg = _activate_with_adapter(_ValidAdapter(), tmp_path, pools)

    assert reg.kinds() == ["refkind"]
    assert not pools[0].closed


def test_no_adapter_is_not_a_contract_failure(tmp_path):
    """`create_scenario_adapter` returning None means the kind opts out of scenarios."""
    pools: list[_TrackingPool] = []
    reg = _activate_with_adapter(None, tmp_path, pools)

    assert reg.kinds() == ["refkind"]
    assert reg.get_plugin("refkind").capabilities == frozenset()
