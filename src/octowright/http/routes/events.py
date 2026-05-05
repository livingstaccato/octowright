# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Event-stream endpoints: events / console / downloads + WS tail."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from starlette.endpoints import WebSocketEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .. import state
from ..discovery import (
    _find_recording_for,
    _live_session_or_none,
    _resolve_log_path,
    _tail_jsonl,
)
from ..exposure import guard_sensitive_http, sensitive_allowed_for_connection
from ._common import _paginate, _parse_since


async def session_events(request: Request) -> JSONResponse:
    sid = request.path_params["id"]
    log_path = _resolve_log_path(sid)
    if log_path is None:
        return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
    raw_since = request.query_params.get("since")
    try:
        since = int(raw_since) if raw_since is not None else 0
    except ValueError:
        return JSONResponse({"error": f"invalid since={raw_since!r}, must be int"}, status_code=400)
    return JSONResponse(_tail_jsonl(log_path, since))


# ---------------------------------------------------------------------------
# /console and /downloads — cursor-paginated views over per-session lists
# ---------------------------------------------------------------------------


def _read_console_from_jsonl(jsonl_path: Path) -> list[dict[str, Any]]:
    """Reconstruct console messages from persisted ``action: "console"`` rows."""
    out: list[dict[str, Any]] = []
    if not jsonl_path.exists():
        return out
    try:
        with jsonl_path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if entry.get("action") != "console":
                    continue
                message = {
                    "level": entry.get("level"),
                    "text": entry.get("text", ""),
                }
                if "page_index" in entry:
                    message["page_index"] = entry.get("page_index")
                out.append(message)
    except OSError:
        return out
    return out


def _read_downloads_from_jsonl(jsonl_path: Path) -> list[dict[str, Any]]:
    """Reconstruct download records from a JSONL recording.

    ``BrowserSession._handle_download`` → ``downloads.save_download`` records an
    ``action: "download_saved"`` row with the same field shape used in-memory
    (``url``, ``suggested_filename``, ``path``, ``timestamp``).
    """
    out: list[dict[str, Any]] = []
    if not jsonl_path.exists():
        return out
    try:
        with jsonl_path.open(encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if entry.get("action") != "download_saved":
                    continue
                out.append(
                    {
                        "url": entry.get("url"),
                        "suggested_filename": entry.get("suggested_filename"),
                        "path": entry.get("path"),
                        "timestamp": entry.get("timestamp"),
                    }
                )
    except OSError:
        return out
    return out


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

    # Annotate each record with whether the file is still on disk.
    annotated: list[dict[str, Any]] = []
    for d in downloads:
        path = d.get("path")
        path_exists = isinstance(path, str) and Path(path).exists()
        annotated.append({**d, "path_exists": path_exists})

    sliced, total, cursor = _paginate(annotated, since)
    return JSONResponse({"downloads": sliced, "cursor": cursor, "total": total})


# ---------------------------------------------------------------------------
# WebSocket: live tail
# ---------------------------------------------------------------------------


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
            # Either a closed session (recording present) or unknown.
            jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
            if jsonl is not None:
                await websocket.close(
                    code=1003,
                    reason="closed sessions don't support tail; use GET /api/sessions/{id}/events instead",
                )
            else:
                await websocket.close(code=1008, reason=f"no session with id {sid}")
            return

        log_path = Path(live_session.log_path)
        # Start where the caller's history fetch left off, if they pass it.
        # Without this, the first WS push replays everything from byte 0 and
        # the dashboard renders the launch event twice (once from the initial
        # GET /events, once from the tail's first frame).
        raw_since = websocket.query_params.get("since")
        try:
            cursor = int(raw_since) if raw_since is not None else 0
        except ValueError:
            cursor = 0
        try:
            while True:
                snapshot = _tail_jsonl(log_path, cursor)
                cursor = snapshot["cursor"]
                still_live = _live_session_or_none(sid) is not None
                payload = {
                    "events": snapshot["events"],
                    "cursor": cursor,
                    "complete": (not still_live),
                }
                await websocket.send_json(payload)
                if not still_live:
                    # Live → closed mid-connection: one final push then close.
                    await websocket.close()
                    return
                await asyncio.sleep(state.TAIL_POLL_SECONDS)
        except WebSocketDisconnect:
            return


def routes() -> list[Route | WebSocketRoute]:
    return [
        Route("/api/sessions/{id}/events", guard_sensitive_http(session_events), methods=["GET"]),
        Route("/api/sessions/{id}/console", guard_sensitive_http(session_console), methods=["GET"]),
        Route("/api/sessions/{id}/downloads", guard_sensitive_http(session_downloads), methods=["GET"]),
        WebSocketRoute("/api/sessions/{id}/tail", TailEndpoint),
    ]
