# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins.discovery import DiscoveredPlugin
from octowright.plugins.loader import activate, resolve_descriptors
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from octowright.server import profiles
from octowright.server._plugin_activation import _unregister_profile


@pytest.fixture(autouse=True)
def _clean_plugin_profiles():
    profiles.reset_plugin_profiles()
    yield
    profiles.reset_plugin_profiles()


def test_plugin_profile_widens_the_allowed_set():
    profiles.register_plugin_profile("refkinds", ["refkind_launch", "refkind_close"])
    allowed = profiles.build_allowed_set("refkinds")
    assert {"refkind_launch", "refkind_close"} <= allowed
    assert allowed >= profiles.ALWAYS_ON_TOOLS


def test_plugin_profile_does_not_trigger_unknown_diagnostics(caplog):
    profiles.register_plugin_profile("refkinds", ["refkind_launch"])
    with caplog.at_level("WARNING"):
        profiles.build_allowed_set("refkinds")
    messages = [record.getMessage() for record in caplog.records]
    assert not any("profile.unknown" in message for message in messages)
    assert not any("profile.all_unknown" in message for message in messages)


def test_a_genuinely_unknown_profile_still_warns(caplog):
    with caplog.at_level("WARNING"):
        profiles.build_allowed_set("nosuchprofile")
    messages = [record.getMessage() for record in caplog.records]
    assert any("profile.unknown" in message for message in messages)


def test_plugin_profile_may_not_shadow_a_core_profile():
    with pytest.raises(ValueError, match="core profile"):
        profiles.register_plugin_profile("core", ["refkind_launch"])


def test_registered_names_are_listed():
    profiles.register_plugin_profile("refkinds", ["refkind_launch"])
    assert profiles.plugin_profile_names() == ["refkinds"]


def test_unregister_is_idempotent():
    profiles.unregister_plugin_profile("never-registered")
    profiles.register_plugin_profile("refkinds", ["refkind_launch"])
    profiles.unregister_plugin_profile("refkinds")
    profiles.unregister_plugin_profile("refkinds")
    assert profiles.plugin_profile_names() == []


class _FailingDescriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names = frozenset({"refkind_launch"})
    tool_module = None
    profile_name = "refkinds"
    frontend = None

    def create_pool(self, ctx: Any) -> Any:
        raise RuntimeError("pool refused")

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _FakeEP:
    name = "refkind"
    value = "m:p"

    def load(self) -> Any:
        return _FailingDescriptor()


class _FakeToolManager:
    def __init__(self) -> None:
        self._tools: dict[str, object] = {}


def test_rollback_unregisters_the_plugins_profile(tmp_path):
    # Spec §6.3's rollback unregisters the profile too. Leaving it behind makes
    # OCTOWRIGHT_PROFILE=<its name> resolve to a set naming tools that do not
    # exist. The hook comes from the server layer because octowright.plugins
    # sits below octowright.server and must not import server.profiles.
    profiles.register_plugin_profile("refkinds", ["refkind_launch"])
    assert profiles.plugin_profile_names() == ["refkinds"]

    registry = PluginRegistry()
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=_FakeEP())
    resolved = resolve_descriptors(registry=registry, discovered=[found], enabled=["refkind"])

    activate(
        registry=registry,
        resolved=resolved,
        ctx_factory=lambda kind: PluginContext(kind=kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use),
        tool_manager=_FakeToolManager(),
        on_rollback=_unregister_profile,
    )

    assert registry.kinds() == []
    assert profiles.plugin_profile_names() == []
