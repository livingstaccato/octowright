# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Stdio↔HTTP MCP bridge for follower octowright instances.

When a second ``octowright serve`` starts and finds a live leader in the
lockfile, it doesn't open browsers of its own — it becomes a transparent
proxy. Stdin frames (from the MCP client) are forwarded to the leader's
streamable-HTTP ``/mcp`` endpoint, and the leader's responses are written
back to stdout.

The bridge owns no state. If the leader goes away mid-session, the bridge
exits cleanly and the MCP client sees its stdio server close. An optional
``health_url`` arms a watchdog task that tears the bridge down if the
leader's HTTP debugger stops answering — that's how we recover when a
wedged leader silently stops pumping responses without closing its SSE
stream.
"""

from __future__ import annotations

from octowright.defaults import BRIDGE_HEALTH_INTERVAL_SECONDS, BRIDGE_HEALTH_MAX_FAILURES
from octowright.proxy_supervisor import run_supervised_proxy


async def run_proxy(
    leader_mcp_url: str,
    *,
    health_url: str | None = None,
    heartbeat_interval: float = BRIDGE_HEALTH_INTERVAL_SECONDS,
    heartbeat_max_failures: int = BRIDGE_HEALTH_MAX_FAILURES,
) -> None:
    """Forward this process's stdio MCP traffic to ``leader_mcp_url``.

    Returns when either side closes its stream, or — if ``health_url`` is
    provided — when the watchdog observes ``heartbeat_max_failures`` consecutive
    failed probes (each with a 5s timeout) at ``heartbeat_interval`` cadence.

    Raises whatever the underlying transports raise on connection failure —
    ``cli.serve`` is expected to catch and fall back to leader mode if the
    leader has died.

    NOTE: The actual stdio↔HTTP pump, request bookkeeping, and leader-health
    watchdog all live in ``octowright.proxy_supervisor``. This module is a
    thin facade preserved for the historical public entry point.
    """
    await run_supervised_proxy(
        leader_mcp_url=leader_mcp_url,
        health_url=health_url,
        heartbeat_interval=heartbeat_interval,
        heartbeat_max_failures=heartbeat_max_failures,
    )
