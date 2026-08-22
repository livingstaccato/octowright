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
