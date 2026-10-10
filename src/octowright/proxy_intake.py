# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The follower's stdin side: keep reading while a forwarded call waits.

Frames from the MCP client go to the leader one at a time, in order, and a
frame with no leader to go to yet -- the election is still running, or the
leader is being reconnected to -- waits for one. Reading stdin in that same
loop meant everything behind the waiting call waited too, ``ping`` included,
so a client that pings to check the server is alive saw it hang for as long as
the election took.

``LocalIntake`` therefore splits reading from sending. The reader takes every
frame as it arrives; the sender forwards them one at a time in arrival order,
exactly as before. Two kinds are handled by the reader itself:

- ``ping``, when it would otherwise wait behind something (or for a leader),
  is answered here at once. With nothing in the way it still goes to the
  leader, as it always has.
- ``notifications/cancelled`` for a call that has not reached a leader is the
  follower's to honour: the call is withdrawn -- dropped from the queue, or its
  wait for a writer abandoned -- and the leader never hears of either. MCP
  says the receiver of a cancellation does not answer the cancelled request,
  and here the follower is that receiver. A call the leader already has keeps
  its cancellation in the queue, in order, and stops being the bridge's to
  resume or time out (``BridgeSupervisor.forget_in_flight``).

What waits is bounded by ``MAX_PENDING_FRAMES``. The reader never waits on the
sender (a ping behind a waiting call would wait again), so past the cap a
request is answered with a bridge error at once and a notification is dropped
with a warning, rather than holding without limit everything a client sends
while no leader is reachable.
"""

from __future__ import annotations

import math
from typing import Any

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCNotification, JSONRPCResponse
from provide.telemetry import get_logger

from octowright._bridge_message_helpers import (
    bridge_error,
    is_request,
    message_method,
    message_request_id,
    message_root,
)

log = get_logger(__name__)

# Frames accepted and not yet sent that the reader will hold. A client waits
# for answers, so in practice only a handful queue; the cap is for a client
# that keeps sending through a long election or an unreachable leader.
MAX_PENDING_FRAMES = 1024


def cancelled_request_id(message: SessionMessage) -> str | int | None:
    """The ``requestId`` a ``notifications/cancelled`` names, else None."""
    root = message_root(message)
    if not isinstance(root, JSONRPCNotification) or root.method != "notifications/cancelled":
        return None
    params = root.params
    request_id = params.get("requestId") if isinstance(params, dict) else None
    return request_id if isinstance(request_id, (str, int)) else None


class LocalIntake:
    """Read the client's frames continuously; forward them in order."""

    def __init__(self, supervisor: Any, remote_write_slot: Any) -> None:
        self._supervisor = supervisor
        self._slot = remote_write_slot
        # Unbounded as a stream: the reader must never wait on the sender, or a
        # ping behind a waiting call waits again. ``accept`` bounds it instead.
        self._send, self._recv = anyio.create_memory_object_stream[SessionMessage](math.inf)
        # Frames accepted and not yet finished by the sender.
        self._pending = 0
        # Requests accepted and not yet taken by the sender, and the subset a
        # cancellation withdrew (skipped when the sender reaches them).
        self._queued: set[str | int] = set()
        self._withdrawn: set[str | int] = set()

    async def run(self, local_read: Any) -> None:
        """Read until stdin ends, then return once everything read is sent."""
        async with anyio.create_task_group() as tg:
            tg.start_soon(self._drain)
            async with self._send:
                async for message in local_read:
                    await self.accept(message)

    async def accept(self, message: SessionMessage) -> None:
        if await self._answer_ping_now(message):
            return
        cancelled = cancelled_request_id(message)
        if cancelled is not None and self._withdraw(cancelled):
            return
        request_id = message_request_id(message) if is_request(message) else None
        if self._pending >= MAX_PENDING_FRAMES:
            await self._refuse(message, request_id)
            return
        if request_id is not None:
            self._queued.add(request_id)
        self._pending += 1
        self._send.send_nowait(message)

    async def _answer_ping_now(self, message: SessionMessage) -> bool:
        if not (is_request(message) and message_method(message) == "ping"):
            return False
        if self._pending == 0 and self._slot.write is not None:
            return False
        request_id = message_request_id(message)
        if request_id is None:
            return False
        await self._supervisor.local_write.send(
            SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request_id, result={}))
        )
        return True

    async def _refuse(self, message: SessionMessage, request_id: str | int | None) -> None:
        log.warning(
            "octowright.bridge.intake_full",
            pending=self._pending,
            method=message_method(message),
            request_id=request_id,
        )
        if request_id is not None:
            reason = f"{self._pending} messages are already waiting for the leader; this one was not queued."
            await self._supervisor.local_write.send(bridge_error(request_id, reason))

    def _withdraw(self, request_id: str | int) -> bool:
        """Honour a cancellation here; True when it must not be forwarded.

        A call still queued, or waiting for a writer, is withdrawn: it never
        reached a leader, so neither it nor its cancellation goes anywhere. A
        call a leader has is forgotten by the bridge and its cancellation is
        forwarded (False), as is one the bridge does not know.
        """
        if request_id in self._queued:
            self._withdrawn.add(request_id)
        elif not self._supervisor.withdraw_waiting(request_id):
            self._supervisor.forget_in_flight(request_id)
            return False
        log.debug("octowright.bridge.call_withdrawn", request_id=request_id)
        return True

    async def _drain(self) -> None:
        async with self._recv:
            async for message in self._recv:
                request_id = message_request_id(message) if is_request(message) else None
                try:
                    if request_id is not None:
                        self._queued.discard(request_id)
                        if request_id in self._withdrawn:
                            self._withdrawn.discard(request_id)
                            continue
                    await self._supervisor.forward_one_local_message(message, self._slot)
                finally:
                    self._pending -= 1
