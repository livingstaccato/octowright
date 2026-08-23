# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A profile-name collision is a refusal, not something to swallow.

Suppressing the ``ValueError`` produced the worst available outcome: with
``OCTOWRIGHT_PROFILE`` naming the collided profile, ``build_allowed_set``
resolves the CORE profile, so ``_allowed_tools`` excludes every plugin tool and
NOT ONE of the plugin's tools registers — while the plugin still activates and
reports ``state: "enabled"``. Invisible in logs and in ``octowright_status()``.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins.discovery import DiscoveredPlugin
from octowright.plugins.loader import ResolvedDescriptor
from octowright.plugins.registry import PluginRegistry
from octowright.server import _state, profiles


@pytest.fixture(autouse=True)
def _clean_plugin_profiles():
    profiles.reset_plugin_profiles()
    yield
    profiles.reset_plugin_profiles()


class _Descriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names = frozenset({"refkind_launch"})
    tool_module = None
    frontend = None

    def __init__(self, profile_name: str | None) -> None:
        self.profile_name = profile_name

    def create_pool(self, ctx: Any) -> Any:  # pragma: no cover - never reached
        raise AssertionError("a refused descriptor must never reach create_pool")

    def create_scenario_adapter(self, pool: Any) -> Any:  # pragma: no cover - never reached
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:  # pragma: no cover - never reached
        return {}


def _item(name: str, profile_name: str | None) -> ResolvedDescriptor:
    found = DiscoveredPlugin(name=name, distribution="d", version="1", entry_point="m:p", ep=None)
    return ResolvedDescriptor(discovered=found, descriptor=_Descriptor(profile_name))


def test_a_colliding_profile_name_drops_the_descriptor(monkeypatch):
    registry = PluginRegistry()
    monkeypatch.setattr(_state, "plugin_registry", registry)

    kept = _state._register_plugin_profiles([_item("rogue", "core"), _item("good", "refkinds")])

    # The colliding plugin never reaches activate, so it cannot partially load.
    assert [item.discovered.name for item in kept] == ["good"]
    rows = {row["name"]: row for row in registry.status_rows()}
    assert rows["rogue"]["state"] == "failed"
    assert "profile name collision" in rows["rogue"]["reason"]
    assert rows["rogue"]["kind"] == "refkind"
    assert profiles.plugin_profile_names() == ["refkinds"]


def test_a_plugin_without_a_profile_is_kept(monkeypatch):
    registry = PluginRegistry()
    monkeypatch.setattr(_state, "plugin_registry", registry)

    kept = _state._register_plugin_profiles([_item("plain", None)])

    assert [item.discovered.name for item in kept] == ["plain"]
    assert profiles.plugin_profile_names() == []
