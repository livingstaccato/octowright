# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from octowright.plugins.registry import PluginRegistry
from octowright.server import plugin_state


@dataclass
class _Session:
    instance_id: str
    kind: str = "refkind"
    label: str | None = None
    profile: str | None = None
    url: str | None = None
    log_path: Path = Path("/tmp/x.jsonl")
    protected: bool = False
    started_at: str = "2026-08-23T00:00:00Z"
    recorder: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Pool:
    sessions: dict[str, _Session] = field(default_factory=dict)

    def maybe_get(self, instance_id: str) -> _Session | None:
        return self.sessions.get(instance_id)

    def iter_sessions(self):
        return iter(list(self.sessions.values()))


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

    def create_scenario_adapter(self, pool: Any) -> None:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {"id": session.instance_id, "kind": session.kind, "refkind_specific": True}


@pytest.fixture
def registered():
    """Install a one-plugin registry and restore the real one afterwards."""
    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _Pool({"refsess01": _Session("refsess01")})
    reg.register(_Descriptor(), pool=pool, adapter=None, discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg, pool
    finally:
        plugin_state.set_registry(original)


def test_state_exposes_the_live_registry(registered):
    from octowright.http import state

    reg, _ = registered
    assert state.plugin_registry is reg


def test_iter_plugin_sessions_spans_every_registered_pool(registered):
    from octowright.http.routes._session_kinds import iter_plugin_sessions

    assert [s.instance_id for s in iter_plugin_sessions()] == ["refsess01"]


def test_find_plugin_session_returns_kind_and_session(registered):
    from octowright.http.routes._session_kinds import find_plugin_session

    found = find_plugin_session("refsess01")
    assert found is not None
    kind, session = found
    assert kind == "refkind"
    assert session.instance_id == "refsess01"
    assert find_plugin_session("nope") is None


def test_no_plugins_registered_is_an_empty_iteration():
    from octowright.http.routes._session_kinds import find_plugin_session, iter_plugin_sessions

    assert list(iter_plugin_sessions()) == []
    assert find_plugin_session("refsess01") is None
