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
exits cleanly and the MCP client sees its stdio server close.
"""

from __future__ import annotations

import anyio
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.stdio import stdio_server


async def run_proxy(leader_mcp_url: str) -> None:
    """Forward this process's stdio MCP traffic to ``leader_mcp_url``.

    Returns when either side closes its stream. Raises whatever the underlying
    transports raise on connection failure — ``cli.serve`` is expected to
    catch and fall back to leader mode if the leader has died.
    """
    async with (
        streamablehttp_client(leader_mcp_url) as (remote_read, remote_write, _get_sid),
        stdio_server() as (local_read, local_write),
        anyio.create_task_group() as tg,
    ):
        tg.start_soon(_pump, local_read, remote_write)
        tg.start_soon(_pump, remote_read, local_write)


async def _pump(source: anyio.abc.ObjectReceiveStream, sink: anyio.abc.ObjectSendStream) -> None:
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
