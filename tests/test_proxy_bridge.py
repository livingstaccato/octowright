# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the follower→leader stdio↔HTTP bridge facade.

The pump, request bookkeeping, and leader-health watchdog all live in
``octowright.proxy_supervisor`` (see ``test_proxy_supervisor.py``). This
file pins the thin ``run_proxy`` facade — it must forward its kwargs
through to ``run_supervised_proxy`` unchanged.
"""

from __future__ import annotations

import pytest

from octowright import proxy_bridge as _bridge


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_run_proxy_forwards_all_kwargs_to_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_run_supervised_proxy(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr(_bridge, "run_supervised_proxy", fake_run_supervised_proxy)

    await _bridge.run_proxy(
        "http://leader/mcp/",
        health_url="http://leader/api/health",
        heartbeat_interval=3.0,
        heartbeat_max_failures=7,
    )

    assert calls == [
        {
            "leader_mcp_url": "http://leader/mcp/",
            "health_url": "http://leader/api/health",
            "heartbeat_interval": 3.0,
            "heartbeat_max_failures": 7,
        }
    ]
