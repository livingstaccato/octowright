# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-side SSE stream of MCP notification frames for the follower bridge.

The notification emitter (``server/mcp_notifications``) is wired to the stdio
transport, so proactive notifications reach a client only when the leader runs
inline. In the default detached-daemon deployment the client connects over
HTTP-MCP, whose transport has no server-initiated-notification path — so crashes,
driver deaths, and session closes never reached the agent (see
``tests/test_mcp_notifications_daemon_live.py``).

This endpoint closes that gap without coupling to MCP-SDK internals: it streams
the leader's ``session_event_bus`` as Server-Sent Events, each frame carrying the
exact ``{method, params}`` the stdio emitter would send. The follower bridge
consumes this stream (``proxy_runtime.consume_leader_notifications``) and injects
each frame into its local stdio write, so the client sees identical notifications
regardless of transport.

Only the leader runs an HTTP server, so this endpoint is live only there; a
follower has no pool and never serves it. Guarded like the dashboard SSE
(loopback / Host check) — it exposes session lifecycle metadata, not credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from octowright.browser_pool.session_event_bus import session_event_bus
from octowright.defaults import DASHBOARD_DISCONNECT_POLL_SECONDS, DASHBOARD_HEARTBEAT_SECONDS
from octowright.http.bridge_auth import header_token_ok, require_token_enabled
from octowright.http.exposure import guard_sensitive_http
from octowright.server.mcp_notifications import notification_payload


def _sse_data(payload: dict[str, Any]) -> bytes:
    """One SSE frame carrying a JSON-RPC notification payload on the default
    (message) event. The follower parses ``data:`` lines and ignores comments."""
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


async def _wait_for_disconnect(request: Request) -> None:
    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(DASHBOARD_DISCONNECT_POLL_SECONDS)


async def mcp_events_endpoint(request: Request) -> StreamingResponse:
    """Stream ``session_event_bus`` events as MCP notification SSE frames.

    Mirrors the dashboard SSE loop: race each queued event against a disconnect
    watcher, emitting a heartbeat comment during quiet periods so the follower
    can detect a dead connection. The initial ``: ready`` comment lets the
    follower confirm the stream opened before the first real event.
    """

    async def stream() -> Any:
        async with session_event_bus.subscribe() as subscription:
            yield b": ready\n\n"
            disconnect_task = asyncio.create_task(_wait_for_disconnect(request))
            event_task: asyncio.Task[Any] | None = None
            try:
                while not disconnect_task.done():
                    event_task = asyncio.create_task(subscription.get())
                    done, _pending = await asyncio.wait(
                        {event_task, disconnect_task},
                        timeout=DASHBOARD_HEARTBEAT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if disconnect_task in done:
                        event_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await event_task
                        break
                    if event_task in done:
                        yield _sse_data(notification_payload(event_task.result()))
                        continue
                    event_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await event_task
                    yield b": heartbeat\n\n"
            finally:
                if event_task is not None and not event_task.done():
                    event_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await event_task
                disconnect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await disconnect_task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _require_token(
    handler: Callable[[Request], Awaitable[Response]], expected_token: str
) -> Callable[[Request], Awaitable[Response]]:
    """Gate the follower-only mcp-events channel with the capability token, so a
    different-user/sandboxed process that can't read the 0600 lockfile can't
    subscribe to the leader's crash/close/driver event stream. No-op when no
    token is configured or ``OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN`` disables it."""
    if not expected_token:
        return handler

    @functools.wraps(handler)
    async def guarded(request: Request) -> Response:
        if require_token_enabled() and not header_token_ok(request.headers.get("x-octowright-token"), expected_token):
            return JSONResponse({"error": "missing or invalid X-Octowright-Token"}, status_code=403)
        return await handler(request)

    return guarded


def routes(*, mcp_token: str = "") -> list[Route]:
    # Host/origin guard OUTSIDE (reject non-loopback first), token check INSIDE —
    # matching the /mcp guard ordering.
    #
    # pairing_exempt: this follower-only channel already demands the capability
    # token, which is strictly stronger than a dashboard bearer (it requires
    # reading the 0600 lockfile). Layering the pairing gate in front would only
    # change the refusal a follower sees from 403 to 401 without adding any
    # authorization, and the browser dashboard never calls this route.
    endpoint = guard_sensitive_http(_require_token(mcp_events_endpoint, mcp_token), pairing_exempt=True)
    return [Route("/api/mcp-events", endpoint, methods=["GET"])]
