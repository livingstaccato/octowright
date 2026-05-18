# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

BRIDGE_ERROR_CODE = -32000
BRIDGE_ERROR_PREFIX = "Octowright bridge error:"


def message_root(message: SessionMessage) -> Any:
    return message.message.root


def message_request_id(message: SessionMessage) -> str | int | None:
    root = message_root(message)
    if isinstance(root, (JSONRPCRequest, JSONRPCResponse, JSONRPCError)):
        return root.id
    return None


def message_method(message: SessionMessage) -> str | None:
    root = message_root(message)
    if isinstance(root, (JSONRPCRequest, JSONRPCNotification)):
        return root.method
    return None


def is_request(message: SessionMessage) -> bool:
    return isinstance(message_root(message), JSONRPCRequest)


def is_response(message: SessionMessage) -> bool:
    return isinstance(message_root(message), (JSONRPCResponse, JSONRPCError))


def bridge_error(request_id: str | int, reason: str) -> SessionMessage:
    return SessionMessage(
        JSONRPCMessage(
            root=JSONRPCError(
                jsonrpc="2.0",
                id=request_id,
                error=ErrorData(
                    code=BRIDGE_ERROR_CODE,
                    message=f"{BRIDGE_ERROR_PREFIX} {reason}",
                ),
            )
        )
    )


@dataclass
class InFlightRequest:
    request_id: str | int
    method: str | None
    started_at: float
    deadline: float


class BridgeSupervisor:
    def __init__(
        self,
        *,
        local_read: Any,
        local_write: Any,
        request_timeout_seconds: float,
    ) -> None:
        self.local_read = local_read
        self.local_write = local_write
        self.request_timeout_seconds = request_timeout_seconds
        self._in_flight: dict[str | int, InFlightRequest] = {}
        self._initialize_message: SessionMessage | None = None
        self.request_timeouts = 0
        self.last_error: str | None = None
        self.remote_session_id: str | None = None
        self.reconnect_attempts = 0

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    async def forward_local_to_remote(self, remote_write: Any) -> None:
        async for message in self.local_read:
            self.track_local_message(message)
            await remote_write.send(message)

    def track_local_message(self, message: SessionMessage) -> None:
        request_id = message_request_id(message)
        if is_request(message) and message_method(message) == "initialize":
            self._initialize_message = message
        if is_request(message) and request_id is not None:
            now = time.monotonic()
            self._in_flight[request_id] = InFlightRequest(
                request_id=request_id,
                method=message_method(message),
                started_at=now,
                deadline=now + self.request_timeout_seconds,
            )

    async def replay_initialize(self, remote_write: Any) -> None:
        if self._initialize_message is not None:
            await remote_write.send(self._initialize_message)

    async def forward_remote_message(self, message: SessionMessage) -> None:
        request_id = message_request_id(message)
        if request_id is not None:
            self._in_flight.pop(request_id, None)
        await self.local_write.send(message)

    async def watch_deadlines(self, interval: float = 0.01) -> None:
        while True:
            await anyio.sleep(interval)
            now = time.monotonic()
            expired = [item for item in self._in_flight.values() if item.deadline <= now]
            for item in expired:
                self._in_flight.pop(item.request_id, None)
                self.request_timeouts += 1
                self.last_error = f"request {item.request_id!r} timed out while waiting for leader response"
                await self.local_write.send(bridge_error(item.request_id, self.last_error))

    async def fail_all_in_flight(self, reason: str) -> None:
        pending = list(self._in_flight.values())
        self._in_flight.clear()
        self.last_error = reason
        for item in pending:
            await self.local_write.send(bridge_error(item.request_id, reason))
