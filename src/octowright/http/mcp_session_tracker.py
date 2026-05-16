# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Track active MCP streamable-HTTP sessions without reaching into SDK internals.

The previous implementation read ``StreamableHTTPSessionManager._server_instances``
— a private attribute whose layout shifted between SDK releases. This module
instead observes the public streamable-HTTP contract: every MCP request after
``initialize`` carries an ``Mcp-Session-Id`` header, the server sets that
header on the initialize response, and clients terminate sessions with
``DELETE /mcp``. We maintain a ``{session_id → last_seen_ts}`` map; a session
counts as active until it is explicitly deleted or has been silent for
longer than ``MCP_SESSION_TTL``.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The idle watchdog already imposes its own grace period (OCTOWRIGHT_IDLE_GRACE,
# default 300s). A 120s TTL here means a session counts as active for at least
# 2 minutes after its last request, so brief client idle gaps don't oscillate
# the count to zero while LLM activity is genuinely paused.
MCP_SESSION_TTL: Final[float] = 120.0


class McpSessionTracker:
    """Sliding-TTL set of MCP session ids. Thread-safe under a single lock."""

    def __init__(self, ttl: float = MCP_SESSION_TTL) -> None:
        self._ttl = ttl
        self._last_seen: dict[str, float] = {}
        self._lock = Lock()

    def mark_active(self, session_id: str) -> None:
        with self._lock:
            self._last_seen[session_id] = time.monotonic()

    def mark_closed(self, session_id: str) -> None:
        with self._lock:
            self._last_seen.pop(session_id, None)

    def active_count(self) -> int:
        cutoff = time.monotonic() - self._ttl
        with self._lock:
            stale = [sid for sid, ts in self._last_seen.items() if ts < cutoff]
            for sid in stale:
                del self._last_seen[sid]
            return len(self._last_seen)

    def reset(self) -> None:
        with self._lock:
            self._last_seen.clear()


def _extract_session_id_from_headers(
    headers: list[tuple[bytes, bytes]],
) -> str | None:
    for name, value in headers:
        if name.lower() == b"mcp-session-id":
            try:
                return value.decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


class McpSessionTrackingMiddleware:
    """ASGI middleware that updates an ``McpSessionTracker`` from the mounted
    MCP transport's request/response metadata.

    - Any request carrying ``Mcp-Session-Id`` marks that id active (continuation).
    - Any response carrying ``Mcp-Session-Id`` marks that id active (creation
      via the ``initialize`` flow, where the request doesn't yet have an id).
    - A successful ``DELETE`` with a session id marks it closed immediately.
    """

    def __init__(self, app: ASGIApp, tracker: McpSessionTracker) -> None:
        self.app = app
        self.tracker = tracker

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_session_id = _extract_session_id_from_headers(scope.get("headers", []))
        is_delete = scope.get("method", "").upper() == "DELETE"

        if request_session_id and not is_delete:
            self.tracker.mark_active(request_session_id)

        deleted_session_id = request_session_id if is_delete else None

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_session_id = _extract_session_id_from_headers(message.get("headers", []))
                if response_session_id:
                    self.tracker.mark_active(response_session_id)
                if deleted_session_id and 200 <= int(message.get("status", 500)) < 400:
                    self.tracker.mark_closed(deleted_session_id)
            await send(message)

        await self.app(scope, receive, _send)
