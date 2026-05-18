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

from typing import Any

import anyio
import httpx

from octowright.proxy_supervisor import run_supervised_proxy


async def run_proxy(
    leader_mcp_url: str,
    *,
    health_url: str | None = None,
    heartbeat_interval: float = 10.0,
    heartbeat_max_failures: int = 3,
) -> None:
    """Forward this process's stdio MCP traffic to ``leader_mcp_url``.

    Returns when either side closes its stream, or — if ``health_url`` is
    provided — when the watchdog observes ``heartbeat_max_failures`` consecutive
    failed probes (each with a 5s timeout) at ``heartbeat_interval`` cadence.

    Raises whatever the underlying transports raise on connection failure —
    ``cli.serve`` is expected to catch and fall back to leader mode if the
    leader has died.
    """
    await run_supervised_proxy(
        leader_mcp_url=leader_mcp_url,
        health_url=health_url,
        heartbeat_interval=heartbeat_interval,
        heartbeat_max_failures=heartbeat_max_failures,
    )


async def _pump(source: Any, sink: Any) -> None:
    """Forward every message from ``source`` to ``sink`` until either closes."""
    try:
        async for message in source:
            # Exceptions surfaced as values by the SDK — re-raise so the task
            # group can shut down both pumps.
            if isinstance(message, Exception):
                raise message
            await sink.send(message)
    except (anyio.EndOfStream, anyio.ClosedResourceError):
        # Normal close on either side; the task group will tear down its peer.
        return


async def _heartbeat(
    cancel_scope: anyio.CancelScope,
    health_url: str,
    interval: float,
    max_failures: int,
) -> None:
    """Cancel the bridge if the leader stops answering ``health_url``.

    A wedged event loop on the leader can leave the SSE stream silent without
    closing it; the pumps then sit in ``async for`` forever. Polling a cheap
    REST endpoint sidesteps that — when the leader is sick, we tear the bridge
    down so the MCP client sees stdio close (instead of hanging on a tool call).
    """
    failures = 0
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            await anyio.sleep(interval)
            try:
                response = await client.get(health_url)
                ok = response.status_code == 200
            except (httpx.HTTPError, OSError):
                ok = False
            if ok:
                failures = 0
                continue
            failures += 1
            if failures >= max_failures:
                cancel_scope.cancel()
                return
