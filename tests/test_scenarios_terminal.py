# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""server/scenarios.py wires terminal_pool through to ScenarioPool.

These are wiring tests: they replace ``scenario_pool`` with a recorder so we can
assert the MCP tools forward ``terminal_pool`` (and that run_as_test skips
terminal participants), without spinning up real browser/terminal pools.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import octowright.server.scenarios as srv


class _RecordingScenarioPool:
    def __init__(self) -> None:
        self.start_kwargs: dict[str, Any] = {}
        self.stop_kwargs: dict[str, Any] = {}
        self.remap_kwargs: dict[str, Any] = {}

    async def start(self, **kwargs: Any) -> Any:
        self.start_kwargs = kwargs
        return SimpleNamespace(scenario_id="sid", name="n", participants=[])

    async def stop(self, **kwargs: Any) -> Any:
        self.stop_kwargs = kwargs
        return {"scenario_id": kwargs["scenario_id"], "teardown_errors": [], "closed": []}

    def remap_participants(self, **kwargs: Any) -> Any:
        self.remap_kwargs = kwargs
        return {"scenario_id": kwargs["scenario_id"], "applied": [], "count": 0}


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[_RecordingScenarioPool, object]:
    fake = _RecordingScenarioPool()
    sentinel = object()
    monkeypatch.setattr(srv, "scenario_pool", fake)
    monkeypatch.setattr(srv, "pool", object())
    monkeypatch.setattr(srv, "terminal_pool", sentinel)
    monkeypatch.setattr(srv, "publish_dashboard_invalidation_nowait", lambda *_a, **_k: None)
    return fake, sentinel


async def test_scenario_start_forwards_terminal_pool(wired: tuple[_RecordingScenarioPool, object]) -> None:
    fake, sentinel = wired
    await srv.scenario_start(name="x")
    assert fake.start_kwargs["terminal_pool"] is sentinel


async def test_scenario_stop_forwards_terminal_pool(wired: tuple[_RecordingScenarioPool, object]) -> None:
    fake, sentinel = wired
    await srv.scenario_stop(scenario_id="sid")
    assert fake.stop_kwargs["terminal_pool"] is sentinel


def test_scenario_remap_forwards_terminal_pool(wired: tuple[_RecordingScenarioPool, object]) -> None:
    fake, sentinel = wired
    srv.scenario_remap_participants(scenario_id="sid", remaps=[])
    assert fake.remap_kwargs["terminal_pool"] is sentinel


async def test_run_as_test_skips_terminal_participants(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A scenario whose only participant is a terminal: verify macros are
    # browser-only, so run_as_test must skip it (0 results), not error on pool.get.
    live = SimpleNamespace(
        name="t",
        spec=SimpleNamespace(verify={"operator": "assert_thing"}),
        participants=[{"instance_id": "term-0", "kind": "terminal", "role": "operator", "persona": "ops"}],
    )
    monkeypatch.setattr(srv.scenario_pool, "get", lambda scenario_id: live)
    monkeypatch.setattr(srv, "reject_unsafe_path", lambda p, *_a, **_k: Path(p))
    monkeypatch.setattr(srv.runner_mod, "_write_junit", lambda *_a, **_k: None)

    result = await srv.scenario_run_as_test(scenario_id="sid", out_path=str(tmp_path / "report.xml"))
    assert result["total"] == 0  # terminal participant skipped, no verify case emitted
