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

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from octowright.recorder import parse_log_line, tail_log_lines

#: Frames returned in one call when the caller names no limit. A socket can
#: emit thousands per minute; uncapped, one read would be the whole session.
WEBSOCKET_MESSAGES_DEFAULT_LIMIT = 100
#: Ceiling on an explicit limit. A caller wanting more pages the cursor.
WEBSOCKET_MESSAGES_MAX_LIMIT = 1000
#: Chars of ONE frame body returned under ``include_payloads``. A row cap does
#: not bound size -- the lesson ``MACRO_FAILURE_CONSOLE_TEXT_CHARS`` records --
#: and a socket carrying a multi-megabyte frame would otherwise put all of it
#: on the MCP transport in a single entry.
WEBSOCKET_PAYLOAD_MAX_CHARS = 65_536
#: The same cap on a 4-char boundary, so a truncated base64 payload still
#: decodes to a prefix of the frame's bytes. Four base64 chars are three
#: bytes, and cutting anywhere else hands back a string that raises on
#: ``b64decode`` -- which a caller reads as a corrupt capture rather than as
#: the truncation it is. Derived rather than written out, so the rounding
#: stays correct if the cap above is ever set to a non-multiple of 4.
_B64_MAX_CHARS = WEBSOCKET_PAYLOAD_MAX_CHARS // 4 * 4
#: Chars of frame content returned by ONE call, across every frame in it. The
#: row cap alone permits ``limit=1000`` full bodies; this is the bound that
#: does not depend on how big the caller's frames happen to be. Sized above
#: the worst-case default read (100 frames x a 1024-char preview) so an
#: ordinary call never meets it.
WEBSOCKET_MESSAGES_MAX_RESPONSE_CHARS = 131_072

#: Sidecar action names, from ``core_io_mixin._handle_websocket``.
_FRAME_ACTIONS = {"websocket_framesent", "websocket_framereceived"}
#: The one-time marker ``_ws_over_ceiling`` writes when the sidecar hits
#: ``OCTOWRIGHT_WEBSOCKET_MAX_BYTES``. Not a frame, but not noise either: it is
#: the only record that frames were dropped at capture time.
_TRUNCATION_ACTION = "websocket_truncated"
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


def _cap(text: str, limit: int) -> tuple[str, bool]:
    """``(text, was_truncated)``, cut at ``limit`` characters."""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _project_frame(row: dict[str, Any], include_payloads: bool) -> dict[str, Any]:
    """One returned frame: a copy, payload included only when asked for."""
    socket_id = row.get("id")
    frame: dict[str, Any] = {
        "ts": row.get("ts"),
        # Coerced to match the summary's ``id``, which ``_register_websocket``
        # stringifies. Returning the raw recorded value here left a caller
        # joining frames to sockets by dict key or ``==`` matching nothing --
        # and an old recording can still hold the int the fallback once wrote.
        "socket_id": None if socket_id is None else str(socket_id),
        "url": row.get("url"),
        "direction": _DIRECTION.get(row.get("action", ""), "unknown"),
        "size": row.get("payload_size"),
        "preview": row.get("payload_preview", ""),
        "binary": "payload_b64" in row,
    }
    if include_payloads:
        # Text and binary are kept as separate keys rather than one coerced
        # field: a caller decoding base64 must not have to guess whether it is
        # looking at base64 or at text that happens to look like it. A row
        # carries one or the other, never both.
        for key, limit in (("payload_text", WEBSOCKET_PAYLOAD_MAX_CHARS), ("payload_b64", _B64_MAX_CHARS)):
            if key in row:
                frame[key], cut = _cap(str(row[key]), limit)
                if cut:
                    # ``size`` still reports the frame as captured, so a
                    # caller can see how much of it this is.
                    frame["payload_truncated"] = True
    return frame


#: Rough per-row cost of JSON keys, quoting and punctuation, so the budget
#: bounds the RESPONSE rather than only the payload content in it.
_ROW_OVERHEAD_CHARS = 120


def _response_chars(frame: dict[str, Any]) -> int:
    """What this frame costs the response, for the per-call byte budget.

    Every returned string, not just the payload ones. ``url`` in particular is
    chosen by the PAGE, has no length cap, and is repeated on every row -- so
    counting only payloads let a socket with a very long URL return a thousand
    rows of megabytes while the counter stayed under the limit, which is the
    oversized response the budget exists to prevent.
    """
    return _ROW_OVERHEAD_CHARS + sum(len(value) for value in frame.values() if isinstance(value, str))


def _wanted(row: dict[str, Any], socket_id: str | None, direction: str | None) -> bool:
    """Does this frame row belong in the caller's page."""
    if socket_id is not None and str(row.get("id")) != str(socket_id):
        return False
    return direction is None or _DIRECTION.get(row.get("action", "")) == direction


class _Page:
    """One page under construction: the rows kept, and why it stopped.

    ``stopped_at`` is the offset of the first frame NOT returned, which is
    what the next cursor must be. ``None`` means the page consumed the whole
    read window and the window's own end cursor is the right answer.
    """

    def __init__(self, cap: int, include_payloads: bool) -> None:
        self.cap = cap
        self.include_payloads = include_payloads
        self.messages: list[dict[str, Any]] = []
        self.stopped_at: int | None = None
        self.capture_truncated = False
        self.capture_limit_bytes: Any = None
        self._spent = 0

    def collect(self, lines: Iterator[tuple[int, bytes]], socket_id: str | None, direction: str | None) -> None:
        for offset, raw in lines:
            row = parse_log_line(raw)
            if row is None:
                continue
            action = str(row.get("action") or "")
            if action == _TRUNCATION_ACTION:
                self.capture_truncated = True
                self.capture_limit_bytes = row.get("limit_bytes")
                continue
            if action not in _FRAME_ACTIONS or not _wanted(row, socket_id, direction):
                continue
            if not self._add(row, offset):
                return

    def _add(self, row: dict[str, Any], offset: int) -> bool:
        """Keep this frame, or stop the page here. ``False`` stops the scan."""
        if len(self.messages) >= self.cap:
            self.stopped_at = offset
            return False
        frame = _project_frame(row, self.include_payloads)
        cost = _response_chars(frame)
        # ``self.messages and`` so a frame larger than the whole budget is
        # still returned: refusing it would make the caller page forever on a
        # frame that can never fit.
        if self.messages and self._spent + cost > WEBSOCKET_MESSAGES_MAX_RESPONSE_CHARS:
            self.stopped_at = offset
            return False
        self._spent += cost
        self.messages.append(frame)
        return True


def read_frames(
    websocket_path: Path | None,
    *,
    cursor: int = 0,
    socket_id: str | None = None,
    direction: str | None = None,
    include_payloads: bool = False,
    limit: int | None = None,
    capture_truncated: bool = False,
    capture_limit_bytes: int | None = None,
) -> dict[str, Any]:
    """A cursor-paginated page of frames from the sidecar.

    Reads through ``recorder.tail_log_lines`` rather than opening the file
    directly: it already bounds ONE read by bytes, lands the cursor on a line
    boundary, and steps over a single line longer than the window instead of
    freezing -- all of which a socket carrying multi-megabyte frames will
    exercise. Lines rather than parsed events, because both of the bounds
    below stop mid-window and have to say WHERE they stopped.

    Two bounds, and the page ends at whichever is reached first. ``limit``
    caps rows; ``WEBSOCKET_MESSAGES_MAX_RESPONSE_CHARS`` caps the content
    those rows carry, since a row cap alone says nothing about size. Either
    way ``next_cursor`` is the offset of the first frame NOT returned, so the
    next call resumes on it -- naming the end of the read window instead is
    what silently dropped every frame between the cap and the window's end.
    """
    # Clamped here as well as in ``tail_log_lines`` (which guards the actual
    # ``seek``) because the progress check below compares against it: an
    # unclamped negative would make every call look like it advanced.
    cursor = max(0, cursor)
    # A session whose page never opened a socket has no sidecar, which is the
    # common case rather than an error: it reads as an empty window, so it
    # takes the same path out and there is one result shape to maintain.
    if websocket_path is None:
        lines: Iterator[tuple[int, bytes]] = iter(())
        window_cursor, total_bytes = cursor, 0
    else:
        lines, window_cursor, total_bytes = tail_log_lines(websocket_path, cursor)

    # Seeded by the session, because the ``websocket_truncated`` marker is
    # written ONCE at the end of the sidecar: detecting it only when a page's
    # own window happens to contain it made the answer page-local, so a caller
    # paging past it -- or polling from the cursor that page returned -- was
    # told the capture was complete. The marker still stands on its own for a
    # reader working from the file alone.
    collected = _Page(resolve_message_limit(limit), include_payloads)
    collected.capture_truncated = capture_truncated
    collected.capture_limit_bytes = capture_limit_bytes
    collected.collect(lines, socket_id, direction)
    next_cursor = window_cursor if collected.stopped_at is None else collected.stopped_at
    # ``_read_window`` deliberately HOLDS the cursor on an unterminated
    # trailing line: the writer is mid-frame and those bytes are not safe to
    # parse yet. So "more bytes exist" and "paging again will get you some"
    # are different questions, and answering the second with the first told a
    # caller to poll forever against a cursor that could not move.
    advanced = next_cursor > cursor

    return {
        "messages": collected.messages,
        "next_cursor": next_cursor,
        "returned": len(collected.messages),
        # "Page again and you will get more." Both ways a page can be short
        # qualify -- a bound stopped it, or the read window cut the file --
        # but only when this call actually moved the cursor, or the answer is
        # an instruction to loop.
        "truncated": collected.stopped_at is not None or (advanced and next_cursor < total_bytes),
        # The raw fact, for a caller watching a live stream who wants to know
        # the writer is mid-line rather than finished.
        "more_on_disk": next_cursor < total_bytes,
        "total_bytes": total_bytes,
        # Frames dropped at CAPTURE time, which no cursor can recover. Read
        # off the marker rather than inferred, and reported rather than
        # silently skipped as a non-frame row.
        "capture_truncated": collected.capture_truncated,
        "capture_limit_bytes": collected.capture_limit_bytes,
    }


def summarize_sockets(registry: dict[str, dict[str, Any]], dropped: int) -> dict[str, Any]:
    """Open and closed sockets with their frame counts.

    Partitioned by liveness rather than returned as one list, because the
    question this answers is "what is connected right now" -- a caller wanting
    to tap a live stream needs the open ones, and burying them among finished
    sockets makes them look the same.
    """
    # ``list(registry.values())`` copies the LIST, not the dicts inside it,
    # and the frame handler keeps mutating those -- so a caller that stashed a
    # summary watched its counts change underneath, and one that edited an
    # entry rewrote the session's own registry for every later reader. The
    # same defect, fixed the same way, as ``_select_console_tail``.
    entries = [dict(entry) for entry in registry.values()]
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
