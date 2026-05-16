# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Memory profiling benchmarks for Octowright."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from octowright import recorder
from octowright.scenarios import LiveScenario, Participant, Scenario


async def profile_tail_log():
    """Stress test tail_log with large data."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "large.jsonl"
        with path.open("w") as f:
            for i in range(10000):
                f.write(json.dumps({"index": i, "data": "a" * 100}) + "\n")

        cursor = 0
        for _ in range(10):
            _events, cursor, _total_bytes = recorder.tail_log(path, cursor)


async def profile_scenario_parallel():
    """Mock parallel scenario startup."""
    # This just exercises the gather logic
    live = MagicMock(spec=LiveScenario)
    live.scenario_id = "test-scenario"
    live.participants = [{"instance_id": f"inst-{i}", "persona": f"p-{i}"} for i in range(20)]
    live.spec = MagicMock(spec=Scenario)
    live.spec.participants = [MagicMock(spec=Participant) for _ in range(20)]

    browser_pool = MagicMock()
    browser_pool.get = MagicMock(return_value=MagicMock())

    from octowright import scenarios, scenarios_pool

    # Mock resolve_startup_macros to return a list
    scenarios.resolve_startup_macros = MagicMock(return_value=["test-macro"])

    # Mock run_macro
    from octowright import macros

    macros.run_macro = AsyncMock()

    await scenarios_pool._run_startup_macros(browser_pool, live)


if __name__ == "__main__":

    async def main():
        print("Profiling tail_log...")
        await profile_tail_log()
        print("Profiling scenario parallel...")
        await profile_scenario_parallel()

    asyncio.run(main())
