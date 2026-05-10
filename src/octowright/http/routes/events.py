# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Event-stream endpoints: events / console / downloads + WS tail."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from starlette.endpoints import WebSocketEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from octowright.http import state
from octowright.http.dashboard_events import dashboard_events
from octowright.http.discovery import (
    _find_recording_for,
    _live_session_or_none,
    _resolve_log_path,
    _tail_jsonl,
)
from octowright.http.exposure import guard_sensitive_http, sensitive_allowed_for_connection
from octowright.http.routes._common import _paginate, _parse_since
from octowright.http.session_artifacts import session_artifact_cache

DASHBOARD_DISCONNECT_POLL_SECONDS = 0.05
DASHBOARD_HEARTBEAT_SECONDS = 15.0


def _sse_frame(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _sse_comment(comment: str) -> bytes:
    return f": {comment}\n\n".encode()


async def _wait_for_dashboard_disconnect(request: Request) -> None:
    while True:
        if await request.is_disconnected():
            return
        await asyncio.sleep(DASHBOARD_DISCONNECT_POLL_SECONDS)


async def dashboard_events_endpoint(request: Request) -> StreamingResponse:
    async def stream() -> Any:
        async with dashboard_events.subscribe() as subscription:
            yield _sse_frame("hello", {"ok": True})
            disconnect_task = asyncio.create_task(_wait_for_dashboard_disconnect(request))
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
                        yield _sse_frame("invalidate", event_task.result())
                        continue
                    event_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await event_task
                    yield _sse_comment("heartbeat")
            finally:
                disconnect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await disconnect_task

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def session_events(request: Request) -> JSONResponse:
    sid = request.path_params["id"]
    log_path = _resolve_log_path(sid)
    if log_path is None:
        return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
    since, err = _parse_since(request)
    if err is not None:
        return err
    assert since is not None  # narrow for type-checker
    return JSONResponse(_tail_jsonl(log_path, since))


# ---------------------------------------------------------------------------
# /console and /downloads — cursor-paginated views over per-session lists
# ---------------------------------------------------------------------------


def _read_console_from_jsonl(jsonl_path: Path) -> list[dict[str, Any]]:
    """Reconstruct console messages from persisted ``action: "console"`` rows.

    Routes through ``SessionArtifactCache.get_console_rows`` so the result of
    a fallback scan (when no sidecar exists yet) is cached in-memory by JSONL
    signature. Subsequent requests against the same recording skip the scan.
    """
    return session_artifact_cache.get_console_rows(jsonl_path)


def _read_downloads_from_jsonl(jsonl_path: Path) -> list[dict[str, Any]]:
    """Reconstruct download records from a JSONL recording.

    ``BrowserSession._handle_download`` → ``downloads.save_download`` records an
    ``action: "download_saved"`` row with the same field shape used in-memory
    (``url``, ``suggested_filename``, ``path``, ``timestamp``). Routes through
    ``get_download_rows`` for the same in-memory caching as console rows.
    """
    return session_artifact_cache.get_download_rows(jsonl_path)


async def session_console(request: Request) -> JSONResponse:
    """Return paginated console messages for a session.

    Live sessions read straight from ``pool.get(id).console``. Closed sessions
    scan the JSONL recording for persisted ``action: "console"`` rows. Optional
    ``level=`` filters by log level (case-sensitive). Optional ``since=`` is a
    0-based index; the response's ``cursor`` is always the new total so callers
    can pass it on the next poll.

    404 when the id is not in the live pool AND no recording is on disk.
    """
    sid = request.path_params["id"]
    since, err = _parse_since(request)
    if err is not None:
        return err
    assert since is not None  # narrow for type-checker

    live = _live_session_or_none(sid)
    if live is not None:
        messages: list[dict[str, Any]] = list(live.console)
    else:
        jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
        if jsonl is None:
            return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
        messages = _read_console_from_jsonl(jsonl)

    level = request.query_params.get("level")
    if level is not None:
        messages = [m for m in messages if m.get("level") == level]

    sliced, total, cursor = _paginate(messages, since)
    return JSONResponse({"messages": sliced, "cursor": cursor, "total": total})


async def session_downloads(request: Request) -> JSONResponse:
    """Return paginated downloads for a session.

    Live sessions use ``pool.get(id).list_downloads()``. Closed sessions scan
    the JSONL recording for ``action: "download_saved"`` rows. Each row gets a
    boolean ``path_exists`` field reflecting whether the saved file is still
    present on disk (users sometimes move the artefact post-run).

    404 when the id is not in the live pool AND no recording is on disk.
    """
    sid = request.path_params["id"]
    since, err = _parse_since(request)
    if err is not None:
        return err
    assert since is not None

    live = _live_session_or_none(sid)
    if live is not None:
        downloads: list[dict[str, Any]] = list(live.list_downloads())
    else:
        jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
        if jsonl is None:
            return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
        downloads = _read_downloads_from_jsonl(jsonl)

    # Paginate first, then stat-annotate only the visible slice. Stat-ing all N
    # records before pagination would be O(total) syscalls per page even when
    # the page only renders ~50 rows.
    sliced, total, cursor = _paginate(downloads, since)
    annotated = [
        {**d, "path_exists": isinstance(d.get("path"), str) and session_artifact_cache.path_exists(d["path"])}
        for d in sliced
    ]
    return JSONResponse({"downloads": annotated, "cursor": cursor, "total": total})


# ---------------------------------------------------------------------------
# WebSocket: live tail
# ---------------------------------------------------------------------------


async def _close_for_unknown_or_closed_session(websocket: WebSocket, sid: str) -> None:
    """Sub-1008/1003 handshake when the session isn't tail-able."""
    jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
    if jsonl is not None:
        await websocket.close(
            code=1003,
            reason="closed sessions don't support tail; use GET /api/sessions/{id}/events instead",
        )
    else:
        await websocket.close(code=1008, reason=f"no session with id {sid}")


def _parse_since_cursor(raw_since: str | None) -> int:
    """Honor `?since=N` to skip events the caller already fetched. Without
    this, the first WS push replays everything from byte 0 and the dashboard
    renders the launch event twice.

    Non-int / missing → 0 (WS can't return a 400; coerce silently). Negative
    values clamp to 0 because ``tail_log`` does ``fh.seek(cursor)``, and a
    negative seek raises ``OSError: Invalid argument`` — a 500 from a
    malformed query param.
    """
    try:
        value = int(raw_since) if raw_since is not None else 0
    except ValueError:
        return 0
    return max(0, value)


async def _stream_tail(websocket: WebSocket, sid: str, log_path: Path, cursor: int) -> None:
    """Live-tail loop. Sends only when there's something new (events arrived
    or the session transitioned live→closed); a TAIL_HEARTBEAT_SECONDS-bounded
    keepalive frame goes out during quiet periods so the client can detect a
    dead connection. Without the empty-frame skip, every poll tick pushed
    serialization+network work for N idle dashboards."""
    ticks_since_heartbeat = 0
    heartbeat_every = max(1, int(state.TAIL_HEARTBEAT_SECONDS / state.TAIL_POLL_SECONDS))
    while True:
        snapshot = _tail_jsonl(log_path, cursor)
        cursor = snapshot["cursor"]
        still_live = _live_session_or_none(sid) is not None
        ticks_since_heartbeat += 1
        if snapshot["events"] or (not still_live) or ticks_since_heartbeat >= heartbeat_every:
            await websocket.send_json({"events": snapshot["events"], "cursor": cursor, "complete": (not still_live)})
            ticks_since_heartbeat = 0
        if not still_live:
            # Live → closed mid-connection: final push above, then close.
            await websocket.close()
            return
        await asyncio.sleep(state.TAIL_POLL_SECONDS)


class TailEndpoint(WebSocketEndpoint):
    """Push JSONL events as they're appended to a LIVE session's log.

    Connection semantics:

    - LIVE session: push ``{events, cursor, complete}`` every ``TAIL_POLL_SECONDS``.
      When the session transitions live → closed mid-connection, send one final
      message with ``complete: true`` and close cleanly.
    - CLOSED session (recording on disk, not in pool): close immediately with
      WS code 1003 and a "use GET /events instead" reason. No payload sent.
    - UNKNOWN session (no live, no recording): close immediately with code 1008
      and a "no session with id" reason.

    The frontend opens this WS only for live sessions; closed/unknown rejection
    is a hard guarantee for callers that get the URL wrong.
    """

    encoding = "json"

    async def on_connect(self, websocket: WebSocket) -> None:
        if not sensitive_allowed_for_connection(websocket):
            await websocket.close(code=1008, reason="remote dashboard access is disabled")
            return
        await websocket.accept()
        sid = websocket.path_params["id"]
        live_session = _live_session_or_none(sid)
        if live_session is None:
            await _close_for_unknown_or_closed_session(websocket, sid)
            return

        log_path = Path(live_session.log_path)
        cursor = _parse_since_cursor(websocket.query_params.get("since"))
        try:
            await _stream_tail(websocket, sid, log_path, cursor)
        except WebSocketDisconnect:
            return


def routes() -> list[Route | WebSocketRoute]:
    return [
        Route("/api/dashboard/events", guard_sensitive_http(dashboard_events_endpoint), methods=["GET"]),
        Route("/api/sessions/{id}/events", guard_sensitive_http(session_events), methods=["GET"]),
        Route("/api/sessions/{id}/console", guard_sensitive_http(session_console), methods=["GET"]),
        Route("/api/sessions/{id}/downloads", guard_sensitive_http(session_downloads), methods=["GET"]),
        WebSocketRoute("/api/sessions/{id}/tail", TailEndpoint),
    ]
