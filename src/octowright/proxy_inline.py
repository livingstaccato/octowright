# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Serve an inline leader through a follower bridge that already owns stdio.

A follower opens stdio and answers the handshake before its election ends
(``proxy_runtime.run_supervised_proxy(leader_source=...)``). When the election
falls back to running the leader in this process -- the daemon never answered,
or another instance held the lock and produced no leader -- that leader cannot
open stdio itself: the bridge has it, and the client has already been told the
session is initialized. So the leader is given an in-memory connection instead
of stdio, and the bridge treats it as its remote: the cached handshake is
replayed into it (its answer swallowed), queued calls follow in order, and its
answers and notifications reach the client through the same supervisor.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from mcp.shared.message import SessionMessage

# Runs the leader on the given (read, write) streams until it exits.
InlineLeader = Callable[[tuple[Any, Any]], Awaitable[None]]


async def serve_inline_leader(
    run_leader: InlineLeader,
    supervisor: Any,
    remote_write_slot: Any,
    set_eof_handler: Callable[[Callable[[], Awaitable[None]]], None],
) -> None:
    """Run ``run_leader`` on a memory connection bridged to the local client.

    Returns when the leader exits. A stdin EOF closes only the leader's read
    side (through ``set_eof_handler``): an inline leader that is discoverable
    keeps serving HTTP-MCP to other clients after its own client leaves, as it
    did when it owned stdio, so the bridge must not cancel it.
    """
    to_leader_send, to_leader_recv = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    from_leader_send, from_leader_recv = anyio.create_memory_object_stream[SessionMessage](0)
    async with anyio.create_task_group() as tg:

        async def _leader() -> None:
            try:
                await run_leader((to_leader_recv, from_leader_send))
            finally:
                tg.cancel_scope.cancel()

        async def _reader() -> None:
            async for message in from_leader_recv:
                await supervisor.forward_remote_message(message)

        tg.start_soon(_leader)
        tg.start_soon(_reader)
        # The handshake goes first, as on any connect: a call sent ahead of it
        # would reach an uninitialized session.
        await supervisor.replay_initialize(to_leader_send)
        remote_write_slot.write = to_leader_send
        remote_write_slot.ready.set()
        set_eof_handler(to_leader_send.aclose)
