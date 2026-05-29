# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.proxy_bridge.

The bridge facade is now a one-line delegate to ``run_supervised_proxy``.
Pump / heartbeat / forwarder logic moved to ``proxy_supervisor`` and is
covered by ``test_proxy_supervisor.py``. The remaining branch coverage
here is the kwarg-passthrough for both default and explicit invocations.
"""

from __future__ import annotations

import pytest

from octowright import proxy_bridge as _bridge
from octowright.defaults import BRIDGE_HEALTH_INTERVAL_SECONDS, BRIDGE_HEALTH_MAX_FAILURES


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_run_proxy_delegates_default_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default run_proxy arguments are passed through to the supervisor."""
    calls: list[dict[str, object]] = []

    async def fake_run_supervised_proxy(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr(_bridge, "run_supervised_proxy", fake_run_supervised_proxy)

    await _bridge.run_proxy("http://leader.example/mcp/")
    assert calls == [
        {
            "leader_mcp_url": "http://leader.example/mcp/",
            "health_url": None,
            "heartbeat_interval": BRIDGE_HEALTH_INTERVAL_SECONDS,
            "heartbeat_max_failures": BRIDGE_HEALTH_MAX_FAILURES,
        }
    ]


@pytest.mark.anyio
async def test_run_proxy_delegates_custom_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Custom health and heartbeat settings are passed through to supervisor."""
    calls: list[dict[str, object]] = []

    async def fake_run_supervised_proxy(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr(_bridge, "run_supervised_proxy", fake_run_supervised_proxy)

    await _bridge.run_proxy(
        "http://leader/mcp/",
        health_url="http://leader/api/health",
        heartbeat_interval=2.0,
        heartbeat_max_failures=5,
    )
    assert calls == [
        {
            "leader_mcp_url": "http://leader/mcp/",
            "health_url": "http://leader/api/health",
            "heartbeat_interval": 2.0,
            "heartbeat_max_failures": 5,
        }
    ]
