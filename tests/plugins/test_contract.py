# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from octowright.plugins import contract
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


# ---------------------------------------------------------------------------
# contract_errors: signature verification, not just attribute presence
# ---------------------------------------------------------------------------


class _GoodAdapter:
    """Satisfies the floor and every capability protocol."""

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        return {}

    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
        return None

    async def wait_for_sync(
        self,
        instance_id: str,
        *,
        selector: str | None,
        text: str | None,
        url: str | None,
        timeout_ms: int | None,
    ) -> None:
        return None

    async def set_dialog_policy(self, instance_id: str, policy: str) -> None:
        return None

    async def install_mock_routes(self, instance_id: str, routes: list[dict[str, Any]]) -> None:
        return None


class _FloorOnlyAdapter:
    """Valid: participates in scenarios, claims no optional capability."""

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        return {}


def test_good_adapter_has_no_contract_errors() -> None:
    assert contract.contract_errors(_GoodAdapter()) == []


def test_floor_only_adapter_is_valid() -> None:
    """Claiming no capability is not a defect — it is the common case."""
    assert contract.contract_errors(_FloorOnlyAdapter()) == []
    assert contract.capabilities_of(_FloorOnlyAdapter()) == frozenset()


def test_missing_the_mandatory_floor_is_an_error() -> None:
    class _NoFloor:
        async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
            return None

    problems = contract.contract_errors(_NoFloor())
    assert len(problems) == 1
    assert "resolve_participant" in problems[0]


def test_sync_run_macro_is_caught_though_isinstance_accepts_it() -> None:
    """The exact gap: `isinstance` sees the attribute and stops there."""

    class _SyncMacros(_FloorOnlyAdapter):
        def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
            return None

    adapter = _SyncMacros()
    # isinstance is satisfied, and capabilities_of therefore reports `macros`...
    assert isinstance(adapter, contract.SupportsMacros)
    assert "macros" in contract.capabilities_of(adapter)
    # ...which is precisely why the signature check has to exist.
    problems = contract.contract_errors(adapter)
    assert len(problems) == 1
    assert "run_macro" in problems[0]
    assert "async def" in problems[0]


def test_renamed_keyword_is_caught() -> None:
    class _WrongKeyword(_FloorOnlyAdapter):
        async def run_macro(self, instance_id: str, *, macro: str, args: dict[str, Any]) -> None:
            return None

    problems = contract.contract_errors(_WrongKeyword())
    assert len(problems) == 1
    assert "run_macro" in problems[0]
    assert "name" in problems[0]


def test_non_callable_attribute_is_caught() -> None:
    class _AttributeNotMethod(_FloorOnlyAdapter):
        run_macro = "not a method"

    problems = contract.contract_errors(_AttributeNotMethod())
    assert len(problems) == 1
    assert "run_macro" in problems[0]
    assert "callable" in problems[0]


def test_renamed_positional_is_allowed() -> None:
    """Core passes positionals by position, so their names are the plugin's business."""

    class _RenamedPositional(_FloorOnlyAdapter):
        async def run_macro(self, iid: str, *, name: str, args: dict[str, Any]) -> None:
            return None

    assert contract.contract_errors(_RenamedPositional()) == []


def test_extra_defaulted_parameter_is_allowed() -> None:
    class _ExtraOptional(_FloorOnlyAdapter):
        async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any], timeout_ms: int = 0) -> None:
            return None

    assert contract.contract_errors(_ExtraOptional()) == []


def test_extra_required_parameter_is_caught() -> None:
    """Core never passes it, so every call would raise TypeError."""

    class _ExtraRequired(_FloorOnlyAdapter):
        async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any], run_id: str) -> None:
            return None

    problems = contract.contract_errors(_ExtraRequired())
    assert len(problems) == 1
    assert "run_macro" in problems[0]


def test_every_capability_protocol_is_verified() -> None:
    """Not just `macros`: the check walks the same table `capabilities_of` does."""

    class _AllWrong(_FloorOnlyAdapter):
        async def run_macro(self, instance_id: str) -> None: ...

        async def wait_for_sync(self, instance_id: str) -> None: ...

        async def set_dialog_policy(self, instance_id: str) -> None: ...

        async def install_mock_routes(self, instance_id: str) -> None: ...

    adapter = _AllWrong()
    assert contract.capabilities_of(adapter) == CAPABILITIES  # all four claimed...
    problems = contract.contract_errors(adapter)
    assert len(problems) == 4
    assert {p.removeprefix("core calls ").split("(")[0] for p in problems} == {
        "run_macro",
        "wait_for_sync",
        "set_dialog_policy",
        "install_mock_routes",
    }


def test_inherited_protocol_methods_are_checked() -> None:
    """A refined Protocol must not smuggle its base's methods past the check."""
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class _Base(Protocol):
        async def alpha(self, instance_id: str, *, tag: str) -> None: ...

    @runtime_checkable
    class _Refined(_Base, Protocol):
        async def beta(self, instance_id: str) -> None: ...

    names = {name for name, _ in contract._protocol_methods(_Refined)}
    assert names == {"alpha", "beta"}
