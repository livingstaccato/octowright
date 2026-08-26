# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Structural contract a session-kind plugin implements.

Protocols, not base classes. A plugin implements shapes and never inherits
core's lifecycle assumptions — the same deliberate choice that makes
``TerminalSession`` a parallel dataclass rather than a ``BrowserSession``
subclass.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from octowright.recorder import Recorder

#: Backend contract version. Bumped when any Protocol in this module changes
#: shape. Checked by the loader; a mismatched plugin is refused.
PLUGIN_API_VERSION = 1

#: Frontend renderer contract version. Deliberately separate: collapsing the
#: two makes the dashboard's mismatch fallback unreachable, because a
#: version-mismatched plugin would be refused at load and never reach
#: ``/api/plugins``. Unused until the frontend step; declared here so both
#: versions live in one place.
RENDERER_API_VERSION = 1

#: Closed, core-defined scenario-capability vocabulary. Core must know what a
#: capability means in order to skip it, so a plugin cannot invent one.
#: ``fixtures`` is deliberately absent: ``_validate_fixtures`` accepts exactly
#: ``dialog_policy`` and ``mock_routes``, so a container capability alongside
#: its own constituents means undefined precedence or a double-applied fixture.
CAPABILITIES: frozenset[str] = frozenset({"macros", "sync", "dialog_policy", "mock_routes"})


class LaunchResult(TypedDict, total=False):
    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    log_path: str
    extra: dict[str, Any]


class CloseResult(TypedDict, total=False):
    instance_id: str
    kind: str
    closed: bool
    extra: dict[str, Any]


class SessionRecord(Protocol):
    """One live session owned by a plugin's pool."""

    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    url: str | None
    recorder: Recorder
    log_path: Path
    protected: bool
    extra: dict[str, Any]


class SessionPool(Protocol):
    """The single registry for one kind's live sessions.

    Core keeps no parallel session table: the live list, session detail, and
    close all resolve by iterating registered pools. Instance ids must be
    unique across *all* pools; core enforces that at ``SessionLaunch.commit``.
    """

    async def launch(self, **kwargs: Any) -> LaunchResult: ...

    def get(self, instance_id: str) -> SessionRecord:
        """Return the session, or raise ``KeyError``."""

    def maybe_get(self, instance_id: str) -> SessionRecord | None: ...

    def iter_sessions(self) -> Iterator[SessionRecord]:
        """Iterate a *snapshot* — core iterates while other tasks may launch."""

    async def close(self, instance_id: str, *, force: bool = False) -> CloseResult:
        """Close one session.

        Raises ``KeyError`` for an unknown id and
        ``ProtectedSessionCloseError`` when the session is protected and
        ``force`` is not set.
        """

    async def close_all(self, *, force: bool = False) -> None:
        """Close every session, continuing past an individual failure.

        Raises an aggregate at the end if any close failed — daemon shutdown
        tears every pool down and reports what did not close.
        """


@runtime_checkable
class ScenarioAdapter(Protocol):
    """The mandatory floor for scenario participation."""

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]: ...


@runtime_checkable
class SupportsMacros(Protocol):
    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None: ...


@runtime_checkable
class SupportsSync(Protocol):
    async def wait_for_sync(
        self,
        instance_id: str,
        *,
        selector: str | None,
        text: str | None,
        url: str | None,
        timeout_ms: int | None,
    ) -> None: ...


@runtime_checkable
class SupportsDialogPolicy(Protocol):
    async def set_dialog_policy(self, instance_id: str, policy: str) -> None: ...


@runtime_checkable
class SupportsMockRoutes(Protocol):
    async def install_mock_routes(self, instance_id: str, routes: list[dict[str, Any]]) -> None: ...


#: capability name -> the Protocol whose presence *is* the capability. Core
#: derives support from this table; a plugin never declares a capability
#: string, so it cannot claim one it did not implement.
_CAPABILITY_PROTOCOLS: dict[str, type] = {
    "macros": SupportsMacros,
    "sync": SupportsSync,
    "dialog_policy": SupportsDialogPolicy,
    "mock_routes": SupportsMockRoutes,
}


def capabilities_of(adapter: object) -> frozenset[str]:
    """Derive the capability set an adapter actually implements."""
    return frozenset(name for name, proto in _CAPABILITY_PROTOCOLS.items() if isinstance(adapter, proto))


#: Stand-in argument used to probe an implementation's signature. Never called
#: with, never stored — ``Signature.bind`` only counts and names arguments.
_PROBE = object()


def _protocol_methods(proto: type) -> Iterator[tuple[str, Any]]:
    """Every method a Protocol declares, its own and any it inherits.

    Walked over the MRO rather than read off ``vars(proto)`` so that splitting
    a Protocol into a base and a refinement later does not silently drop the
    inherited half from the check — the failure mode would be a capability that
    looks verified and is not. Reversed so a refinement's override wins.
    """
    found: dict[str, Any] = {}
    for klass in reversed(proto.__mro__):
        for name, value in vars(klass).items():
            if not name.startswith("_") and inspect.isfunction(value):
                found[name] = value
    return iter(found.items())


def _without_self(signature: inspect.Signature) -> inspect.Signature:
    """Drop the receiver, so a declared signature prints the way it is called."""
    return signature.replace(parameters=[p for name, p in signature.parameters.items() if name != "self"])


def _declared_call_shape(declared: inspect.Signature) -> tuple[list[Any], dict[str, Any]]:
    """The exact call shape core makes, derived from the Protocol's own signature.

    Positional parameters become positional probes and keyword-only ones become
    named probes, so an implementation stays free to *rename* its positionals
    (core passes those by position and never names them) while a renamed or
    missing keyword is caught.
    """
    positional: list[Any] = []
    keywords: dict[str, Any] = {}
    for name, param in declared.parameters.items():
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD):
            positional.append(_PROBE)
        elif param.kind is param.KEYWORD_ONLY:
            keywords[name] = _PROBE
    return positional, keywords


def _is_async_callable(obj: Any) -> bool:
    if inspect.iscoroutinefunction(obj):
        return True
    call = getattr(obj, "__call__", None)  # noqa: B004 - a callable object's async-ness lives on __call__
    return call is not None and inspect.iscoroutinefunction(call)


def _protocol_errors(adapter: object, proto: type) -> list[str]:
    problems: list[str] = []
    for name, declared in _protocol_methods(proto):
        impl = getattr(adapter, name, None)
        if not callable(impl):
            problems.append(f"{name}: expected a callable, got {type(impl).__name__}")
            continue
        if inspect.iscoroutinefunction(declared) and not _is_async_callable(impl):
            problems.append(f"{name}: must be `async def` — core awaits it")
            continue
        try:
            signature = inspect.signature(impl)
        except (TypeError, ValueError):
            continue  # not introspectable (a C builtin); unverifiable, so not an error
        declared_signature = _without_self(inspect.signature(declared))
        positional, keywords = _declared_call_shape(declared_signature)
        try:
            signature.bind(*positional, **keywords)
        except TypeError as exc:
            problems.append(f"core calls {name}{declared_signature}, which the implementation rejects: {exc}")
    return problems


def contract_errors(adapter: object) -> list[str]:
    """Why ``adapter`` cannot serve as a scenario adapter — empty when it can.

    ``isinstance`` against a ``runtime_checkable`` Protocol tests attribute
    *presence* and nothing else: not arity, not keyword names, not whether the
    method is a coroutine. So an adapter carrying a ``run_macro`` that is a bare
    attribute, a sync function, or a function taking different keywords passes
    ``capabilities_of`` and is registered as supporting ``macros``. The mismatch
    then surfaces mid-scenario as a ``TypeError`` raised from core's own call
    site — read as a scenario failure, not as the plugin defect it is.

    Checked by *binding* the call shape each Protocol declares against the
    implementation's signature, so the check tracks the Protocol rather than a
    hand-maintained mirror of it: change a Protocol and this follows.

    ``ScenarioAdapter`` is the mandatory floor and its absence is an error;
    the capability protocols are optional, so only the ones an adapter claims
    (by attribute presence) are verified. An adapter that claims none is valid
    — it participates in scenarios and supports no extra capability.
    """
    if not isinstance(adapter, ScenarioAdapter):
        return ["missing resolve_participant() — the mandatory ScenarioAdapter floor"]
    problems = _protocol_errors(adapter, ScenarioAdapter)
    for proto in _CAPABILITY_PROTOCOLS.values():
        if isinstance(adapter, proto):
            problems.extend(_protocol_errors(adapter, proto))
    return problems


@dataclass(frozen=True)
class FrontendAsset:
    """Prebuilt dashboard UI a plugin ships. Consumed in the frontend step."""

    renderer_api_version: int
    asset_dir: Path
    module_path: str
    layout: Literal["browser", "stream"]


class SessionKindPlugin(Protocol):
    """The package-level descriptor an entry point resolves to.

    Every member except ``create_pool`` / ``create_scenario_adapter`` /
    ``session_detail`` is metadata core validates *before* running plugin
    logic.
    """

    kind: str
    display_name: str
    plugin_api_version: int
    tool_names: frozenset[str]
    tool_module: str | None
    profile_name: str | None
    frontend: FrontendAsset | None

    def create_pool(self, ctx: Any) -> SessionPool: ...

    def create_scenario_adapter(self, pool: SessionPool) -> ScenarioAdapter | None:
        """Build the scenario adapter, or ``None`` if the kind cannot participate.

        A factory rather than an attribute because the adapter resolves
        instance ids against the pool, which does not exist until
        ``create_pool`` has run.
        """

    def session_detail(self, session: SessionRecord) -> dict[str, Any]: ...
