# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Websocket observation tools: list live sockets, read the frames they carried.

The capture side has always existed (``session/core_io_mixin._handle_websocket``);
these are the read-back pair it never had. See ``session/websocket_view`` for
why frames come from the sidecar and why payloads are opt-in.
"""

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.session.websocket_view import (
    WEBSOCKET_MESSAGES_DEFAULT_LIMIT,
    WEBSOCKET_MESSAGES_MAX_LIMIT,
)


@mcp.tool(
    structured_output=False,
    description=(
        "Return WebSocket frames this browser's pages have sent and received. All frames are "
        "captured automatically from launch — no setup needed — so this works retroactively on "
        "traffic that already happened. Use it for a real-time app that pushes updates over a "
        "socket instead of polling HTTP (chat, live dashboards, collaborative editors): it is "
        "how you observe that stream without extracting the page's session token. "
        "Each entry has {ts, socket_id, url, direction ('sent'/'received'), size, preview, binary}. "
        "Pass include_payloads=True for the FULL frame body (payload_text, or payload_b64 for "
        "binary) — off by default because a busy socket emits thousands of frames and the "
        "previews are already length-capped. "
        f"At most {WEBSOCKET_MESSAGES_DEFAULT_LIMIT} frames are returned per call (raise or lower "
        f"with `limit`, max {WEBSOCKET_MESSAGES_MAX_LIMIT}); when `truncated` is true, read the "
        "next page by passing the returned next_cursor as `cursor`. Poll with the cursor to "
        "follow a live stream. Narrow with socket_id (from browser_websocket_summary) or "
        "direction. Call browser_websocket_summary first to see which sockets are open."
    ),
)
async def browser_websocket_messages(
    instance_id: str,
    cursor: int = 0,
    socket_id: str | None = None,
    direction: str | None = None,
    include_payloads: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    if direction is not None and direction not in ("sent", "received"):
        raise ValueError(f"direction must be 'sent' or 'received', got {direction!r}")
    async with browser_operation(pool, instance_id, "browser_websocket_messages") as session:
        return session.get_websocket_messages(
            cursor=cursor,
            socket_id=socket_id,
            direction=direction,
            include_payloads=include_payloads,
            limit=limit,
        )


@mcp.tool(
    structured_output=False,
    description=(
        "Summarize the WebSocket connections this browser's pages have opened. Returns {open, "
        "closed, open_count, closed_count, dropped}, where each socket carries {id, url, "
        "opened_at, closed_at, framesent, framereceived, bytes, error}. Open and closed are "
        "reported separately because the usual question is which sockets are live RIGHT NOW. "
        "Use the `id` as socket_id in browser_websocket_messages to read one socket's frames. "
        "`dropped` counts sockets evicted from the bounded registry by a page that opened many "
        "over time (closed ones are evicted first, so live sockets survive a churny page)."
    ),
)
async def browser_websocket_summary(instance_id: str) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_websocket_summary") as session:
        return session.get_websocket_summary()
