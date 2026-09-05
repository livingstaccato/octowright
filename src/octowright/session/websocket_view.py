# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Reading back what a page's websockets carried.

Octowright has always *captured* websocket traffic -- ``page.on("websocket")``
is wired at launch, and every frame lands in the per-session
``.websocket.cache.jsonl`` sidecar with its full payload. Nothing ever read it
back, so a real-time app (an authenticated SPA delivering updates over a
socket instead of polling) left its most interesting traffic on disk with no
way to ask for it. The alternatives were both bad: poll HTTP and lose the
real-time property, or lift the page's session token out of the browser and
replay it in an external client -- which httpOnly cookies defeat, and which
the network capture correctly will not hand over.

This module is the read side only; capture is unchanged. Frames come from the
sidecar rather than an in-memory ring because the sidecar is already the
full-fidelity sink -- an in-memory copy would double the footprint of a
firehose page to serve a question nobody may ask.

**Payloads are previews by default.** A socket carrying an application's event
stream is exactly the shape that fills a context window: the sidecar keeps the
whole payload, and ``include_payloads`` opts into it, mirroring how
``include_headers`` works on the HTTP pair for the same reason.

**Honest scope on redaction:** a frame is application data with no name to
classify on, so unlike a header there is nothing to key a policy off. Previews
are length-capped at capture time; full payloads are opt-in. That bounds
volume, not sensitivity, and is documented rather than dressed up as a scrub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from octowright.recorder import tail_log

#: Frames returned in one call when the caller names no limit. A socket can
#: emit thousands per minute; uncapped, one read would be the whole session.
WEBSOCKET_MESSAGES_DEFAULT_LIMIT = 100
#: Ceiling on an explicit limit. A caller wanting more pages the cursor.
WEBSOCKET_MESSAGES_MAX_LIMIT = 1000

#: Sidecar action names, from ``core_io_mixin._handle_websocket``.
_FRAME_ACTIONS = {"websocket_framesent", "websocket_framereceived"}
#: Caller-facing direction names, which say who sent the frame rather than
#: which Playwright event fired.
_DIRECTION = {"websocket_framesent": "sent", "websocket_framereceived": "received"}


def resolve_message_limit(limit: int | None) -> int:
    """Frame cap for one read. Never unbounded from the tool surface.

    A non-positive value falls back to the default rather than meaning
    "unlimited": zero most plausibly reads as "no opinion", and an LLM must not
    be able to remove the cap by passing it -- the same reasoning
    ``browser_network_requests`` uses.
    """
    if limit is None or limit <= 0:
        return WEBSOCKET_MESSAGES_DEFAULT_LIMIT
    return min(int(limit), WEBSOCKET_MESSAGES_MAX_LIMIT)


def _project_frame(row: dict[str, Any], include_payloads: bool) -> dict[str, Any]:
    """One returned frame: a copy, payload included only when asked for."""
    frame: dict[str, Any] = {
        "ts": row.get("ts"),
        "socket_id": row.get("id"),
        "url": row.get("url"),
        "direction": _DIRECTION.get(row.get("action", ""), "unknown"),
        "size": row.get("payload_size"),
        "preview": row.get("payload_preview", ""),
        "binary": "payload_b64" in row,
    }
    if include_payloads:
        # Text and binary are kept as separate keys rather than one coerced
        # field: a caller decoding base64 must not have to guess whether it is
        # looking at base64 or at text that happens to look like it.
        if "payload_text" in row:
            frame["payload_text"] = row["payload_text"]
        if "payload_b64" in row:
            frame["payload_b64"] = row["payload_b64"]
    return frame


def read_frames(
    websocket_path: Path | None,
    *,
    cursor: int = 0,
    socket_id: str | None = None,
    direction: str | None = None,
    include_payloads: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """A cursor-paginated page of frames from the sidecar.

    Reads through ``recorder.tail_log`` rather than opening the file directly:
    it already bounds ONE read by bytes, lands the cursor on a line boundary,
    and steps over a single line longer than the window instead of freezing --
    all of which a socket carrying multi-megabyte frames will exercise.
    """
    if websocket_path is None:
        return {"messages": [], "next_cursor": cursor, "returned": 0, "truncated": False, "total_bytes": 0}

    rows, next_cursor, total_bytes = tail_log(websocket_path, cursor)
    cap = resolve_message_limit(limit)
    messages: list[dict[str, Any]] = []
    truncated = False
    for row in rows:
        if row.get("action") not in _FRAME_ACTIONS:
            continue
        if socket_id is not None and str(row.get("id")) != str(socket_id):
            continue
        if direction is not None and _DIRECTION.get(row.get("action", "")) != direction:
            continue
        if len(messages) >= cap:
            truncated = True
            break
        messages.append(_project_frame(row, include_payloads))
    return {
        "messages": messages,
        "next_cursor": next_cursor,
        "returned": len(messages),
        "truncated": truncated,
        "total_bytes": total_bytes,
    }


def summarize_sockets(registry: dict[str, dict[str, Any]], dropped: int) -> dict[str, Any]:
    """Open and closed sockets with their frame counts.

    Partitioned by liveness rather than returned as one list, because the
    question this answers is "what is connected right now" -- a caller wanting
    to tap a live stream needs the open ones, and burying them among finished
    sockets makes them look the same.
    """
    entries = list(registry.values())
    open_sockets = [entry for entry in entries if not entry["closed_at"]]
    closed_sockets = [entry for entry in entries if entry["closed_at"]]
    return {
        "open": open_sockets,
        "closed": closed_sockets,
        "open_count": len(open_sockets),
        "closed_count": len(closed_sockets),
        # A shrinking count is otherwise unexplainable; the registry is bounded.
        "dropped": dropped,
    }
