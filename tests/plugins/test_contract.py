# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.plugins.contract import (
    CAPABILITIES,
    PLUGIN_API_VERSION,
    ScenarioAdapter,
    SupportsMacros,
    SupportsSync,
    capabilities_of,
)


def _shape(proto: type) -> set[str]:
    """Every member a Protocol declares in its own body.

    ``dir()`` is no good here: an annotation-only member like
    ``SessionKindPlugin.kind`` never appears in it.
    """
    annotated = set(getattr(proto, "__annotations__", {}))
    methods = {name for name, value in vars(proto).items() if not name.startswith("_") and callable(value)}
    return annotated | methods


class _FloorOnly:
    def resolve_participant(self, spec, persona):
        return {}


class _WithMacros(_FloorOnly):
    async def run_macro(self, instance_id, *, name, args):
        return None


def test_floor_only_adapter_satisfies_the_base_protocol():
    # The whole point of splitting capabilities into separate Protocols: an
    # adapter implementing only the floor must still type-check as one.
    assert isinstance(_FloorOnly(), ScenarioAdapter)


def test_capabilities_are_derived_not_declared():
    assert capabilities_of(_FloorOnly()) == frozenset()
    assert capabilities_of(_WithMacros()) == frozenset({"macros"})


def test_capability_protocols_are_runtime_checkable():
    assert isinstance(_WithMacros(), SupportsMacros)
    assert not isinstance(_WithMacros(), SupportsSync)


def test_vocabulary_is_closed_and_matches_the_protocols():
    # Guards against a capability Protocol being added without extending the
    # vocabulary, or vice versa — the drift this design exists to prevent.
    assert frozenset({"macros", "sync", "dialog_policy", "mock_routes"}) == CAPABILITIES
    assert capabilities_of(object()) == frozenset()


def test_api_version_is_tied_to_the_contract_shape():
    # A contract change that forgets to bump the version fails here: every
    # Protocol's declared member set is spelled out, so adding, renaming, or
    # removing one forces a deliberate edit alongside the version bump.
    from octowright.plugins import contract

    assert PLUGIN_API_VERSION == 1
    assert _shape(contract.SessionPool) == {
        "launch",
        "get",
        "maybe_get",
        "iter_sessions",
        "close",
        "close_all",
    }
    assert _shape(contract.SessionRecord) == {
        "instance_id",
        "kind",
        "label",
        "profile",
        "url",
        "recorder",
        "log_path",
        "protected",
        "extra",
    }
    assert _shape(contract.SessionKindPlugin) == {
        "kind",
        "display_name",
        "plugin_api_version",
        "tool_names",
        "tool_module",
        "profile_name",
        "frontend",
        "create_pool",
        "create_scenario_adapter",
        "session_detail",
    }
    assert _shape(contract.ScenarioAdapter) == {"resolve_participant"}
    assert _shape(contract.SupportsMacros) == {"run_macro"}
    assert _shape(contract.SupportsSync) == {"wait_for_sync"}
    assert _shape(contract.SupportsDialogPolicy) == {"set_dialog_policy"}
    assert _shape(contract.SupportsMockRoutes) == {"install_mock_routes"}
