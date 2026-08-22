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
        """Close every session, continuing past individual failures."""


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
