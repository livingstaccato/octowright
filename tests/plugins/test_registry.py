# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from octowright.plugins.registry import PluginRegistry


@dataclass
class _FakeSession:
    instance_id: str
    kind: str = "refkind"


@dataclass
class _FakePool:
    sessions: dict[str, _FakeSession] = field(default_factory=dict)

    def maybe_get(self, instance_id: str) -> _FakeSession | None:
        return self.sessions.get(instance_id)

    def iter_sessions(self):
        return iter(list(self.sessions.values()))


class _FakeDescriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names = frozenset({"refkind_launch"})
    tool_module = None
    profile_name = "refkinds"
    frontend = None

    def create_pool(self, ctx: Any) -> Any:
        return _FakePool()

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


def test_registered_plugin_is_reachable_by_kind():
    reg = PluginRegistry()
    pool = _FakePool()
    reg.register(_FakeDescriptor(), pool=pool, adapter=None, discovered=None)

    assert reg.kinds() == ["refkind"]
    assert reg.pools() == {"refkind": pool}
    assert reg.get_plugin("refkind").pool is pool


def test_id_lookup_spans_every_pool():
    reg = PluginRegistry()
    pool = _FakePool({"abc": _FakeSession("abc")})
    reg.register(_FakeDescriptor(), pool=pool, adapter=None, discovered=None)

    assert reg.id_in_use("abc") is True
    assert reg.id_in_use("zzz") is False
    assert reg.maybe_get("abc").instance_id == "abc"
    assert reg.maybe_get("zzz") is None


def test_status_rows_carry_enabled_and_failed_states():
    reg = PluginRegistry()
    reg.register(_FakeDescriptor(), pool=_FakePool(), adapter=None, discovered=None)
    reg.record_failure(name="broken", reason="boom", discovered=None)
    reg.record_state(name="ghost", state="missing")

    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "enabled"
    assert rows["refkind"]["kind"] == "refkind"
    assert rows["refkind"]["plugin_api_version"] == 1
    assert rows["broken"]["state"] == "failed"
    assert rows["broken"]["reason"] == "boom"
    # A plugin that raised while importing its own module has no descriptor.
    assert "kind" not in rows["broken"]
    assert rows["ghost"]["state"] == "missing"
