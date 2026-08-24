# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.plugins.contract import CAPABILITIES, SessionPool, capabilities_of
from octowright.plugins.errors import ProtectedSessionCloseError
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from tests.plugins.reference.plugin import plugin


@pytest.fixture
def pool(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind=plugin.kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    return plugin.create_pool(ctx)


@pytest.mark.asyncio
async def test_launch_writes_a_session_start_row(pool):
    result = await pool.launch(label="demo")
    log_path = Path(result["log_path"])
    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    assert rows[0]["action"] == "session_start"
    assert rows[0]["kind"] == "refkind"
    assert rows[1]["action"] == "ref_ready"


@pytest.mark.asyncio
async def test_failed_launch_leaves_no_recording(pool, tmp_path):
    with pytest.raises(RuntimeError):
        await pool.launch(fail=True)
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_protected_close_is_refused_without_force(pool):
    result = await pool.launch(protected=True)
    with pytest.raises(ProtectedSessionCloseError):
        await pool.close(result["instance_id"])
    closed = await pool.close(result["instance_id"], force=True)
    assert closed["closed"] is True


@pytest.mark.asyncio
async def test_unknown_id_raises_key_error(pool):
    with pytest.raises(KeyError):
        pool.get("nope")
    with pytest.raises(KeyError):
        await pool.close("nope")
    assert pool.maybe_get("nope") is None


@pytest.mark.asyncio
async def test_close_all_empties_the_pool(pool):
    await pool.launch()
    await pool.launch()
    await pool.close_all(force=True)
    assert list(pool.iter_sessions()) == []


def test_reference_pool_covers_every_session_pool_method(pool):
    # The anti-decay guard: adding a method to the SessionPool contract
    # without covering it in the reference plugin fails CI.
    required = {"launch", "get", "maybe_get", "iter_sessions", "close", "close_all"}
    declared = {name for name, value in vars(SessionPool).items() if not name.startswith("_") and callable(value)}
    assert declared == required, "SessionPool changed shape; update the reference pool and this set"
    for name in sorted(required):
        assert callable(getattr(pool, name)), f"reference pool is missing {name}"

    # Every non-async method must also be reachable without a running loop.
    assert pool.maybe_get("nope") is None
    assert list(pool.iter_sessions()) == []


def test_reference_plugin_declares_no_capabilities_yet():
    # Partial on purpose: scenario participation arrives in build step 3, and
    # a reference plugin that declared everything would exercise no skip path.
    assert capabilities_of(plugin.create_scenario_adapter(None)) == frozenset()
    assert frozenset({"macros", "sync", "dialog_policy", "mock_routes"}) == CAPABILITIES
