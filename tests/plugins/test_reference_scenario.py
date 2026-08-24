# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.plugins.contract import (
    ScenarioAdapter,
    SupportsDialogPolicy,
    SupportsMacros,
    SupportsMockRoutes,
    SupportsSync,
    capabilities_of,
)
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from octowright.scenarios import Participant
from tests.plugins.reference.plugin import plugin


def _adapter(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind=plugin.kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    pool = plugin.create_pool(ctx)
    return plugin.create_scenario_adapter(pool)


def test_the_reference_adapter_is_the_floor_and_nothing_more(tmp_path):
    adapter = _adapter(tmp_path)
    assert isinstance(adapter, ScenarioAdapter)
    assert not isinstance(adapter, SupportsMacros)
    assert not isinstance(adapter, SupportsSync)
    assert not isinstance(adapter, SupportsDialogPolicy)
    assert not isinstance(adapter, SupportsMockRoutes)
    assert capabilities_of(adapter) == frozenset()


def test_resolve_participant_produces_launch_kwargs_the_pool_accepts(tmp_path):
    adapter = _adapter(tmp_path)
    spec = Participant(persona="ref-rita", kind="refkind", role="monitor", options={"note": "hello"})
    resolved = adapter.resolve_participant(spec, None)
    assert resolved["label"] == "ref-rita"
    assert resolved["profile"] == "ref-rita"
    assert resolved["note"] == "hello", "options pass through opaquely"


async def test_the_resolved_kwargs_actually_launch(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind=plugin.kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    pool = plugin.create_pool(ctx)
    adapter = plugin.create_scenario_adapter(pool)
    spec = Participant(persona="ref-rita", kind="refkind", role="monitor")
    launched = await pool.launch(**adapter.resolve_participant(spec, None))
    assert launched["instance_id"]
    await pool.close(launched["instance_id"], force=True)
