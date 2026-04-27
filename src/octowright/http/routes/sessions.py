# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Session-collection endpoints: list / launch / detail / close / navigate."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...defaults import DEFAULT_URL, SUPPORTED_KINDS
from ...server import _state
from .. import state
from ..discovery import (
    _closed_sessions,
    _find_recording_for,
    _iso,
    _live_session_or_none,
    _live_summary,
    _scan_recording_artefacts,
    _summarise_recording,
)
from ._common import _read_json_body


async def list_sessions(_request: Request) -> JSONResponse:
    pool = _state.pool
    live = [_live_summary(s) for s in pool._sessions.values()]
    live_paths = {s["log_path"] for s in live}
    closed = _closed_sessions(state.RECORDINGS_DIR, live_paths)
    return JSONResponse({"live": live, "closed": closed})


async def session_detail(request: Request) -> JSONResponse:
    sid = request.path_params["id"]
    live = _live_session_or_none(sid)
    if live is not None:
        title = None
        with contextlib.suppress(Exception):
            # `page.title()` is async; we can't await it from a sync code path
            # without blocking. The frontend can call browser_evaluate for live
            # title via MCP if it really wants up-to-date.
            title = None
        detail = {
            **_live_summary(live),
            "video_path": str(live.video_path) if live.video_path else None,
            "trace_path": str(live.trace_path) if live.trace_path else None,
            "action_count": -1,  # unknown without re-reading the file
            "console_count": len(live.console),
            "download_count": len(live.downloads),
            "page_count": len(live.pages),
            "title": title,
        }
        # Action count is cheap to derive from the JSONL on disk; do it once.
        log_path = Path(live.log_path)
        if log_path.exists():
            artefacts = _scan_recording_artefacts(log_path)
            detail["action_count"] = artefacts["action_count"]
        return JSONResponse(detail)

    jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
    if jsonl is None:
        return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
    summary = _summarise_recording(jsonl)
    if summary is None:
        return JSONResponse({"error": f"could not parse recording for id {sid!r}"}, status_code=404)
    artefacts = _scan_recording_artefacts(jsonl)
    detail = {
        **summary,
        "video_path": artefacts["video_path"],
        "trace_path": artefacts["trace_path"],
        "action_count": artefacts["action_count"],
        "console_count": artefacts["console_count"],
        "download_count": artefacts["download_count"],
        "page_count": artefacts["page_count"],
        "title": artefacts["title"],
    }
    if artefacts["url"]:
        detail["url"] = artefacts["url"]
    return JSONResponse(detail)


# ---------------------------------------------------------------------------
# Write endpoints — sessions (launch / close / navigate)
# ---------------------------------------------------------------------------


def _live_summary_from_launch(result: dict[str, Any]) -> dict[str, Any]:
    """Build a SessionSummary-shaped dict for the response of POST /api/sessions.

    The shape mirrors ``_live_summary()`` so dashboard code that consumes
    ``GET /api/sessions``'s ``live[]`` entries can reuse the same parser for
    the launch response.
    """
    log_path = Path(result["log_path"])
    started_at = _iso(log_path.stat().st_ctime) if log_path.exists() else _iso(time.time())
    return {
        "id": result["instance_id"],
        "kind": result["kind"],
        "label": result.get("label"),
        "profile": result.get("profile"),
        "url": result.get("url"),
        "started_at": started_at,
        "live": True,
        "log_path": str(log_path),
    }


async def session_launch(request: Request) -> JSONResponse:
    """POST /api/sessions — launch a new browser session via ``pool.launch(...)``.

    Returns a 201 with the SessionSummary shape used by ``GET /api/sessions``.
    """
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    kind = payload.get("kind")
    if not kind:
        return JSONResponse(
            {"error": "kind is required (one of chromium/firefox/webkit)"},
            status_code=400,
        )
    if kind not in SUPPORTED_KINDS:
        return JSONResponse(
            {"error": f"kind must be one of {list(SUPPORTED_KINDS)}, got {kind!r}"},
            status_code=400,
        )

    launch_kwargs: dict[str, Any] = {
        "kind": kind,
        "url": payload.get("url") or DEFAULT_URL,
        "label": payload.get("label"),
        "profile": payload.get("profile"),
        "viewport_w": payload.get("viewport_w"),
        "viewport_h": payload.get("viewport_h"),
        "headed": payload.get("headed", True),
        "stabilize": payload.get("stabilize", False),
        "record_video": payload.get("record_video", False),
        "trace": payload.get("trace", False),
    }

    pool = _state.pool
    try:
        result = await pool.launch(**launch_kwargs)
    except ValueError as e:
        # pool.launch validates `kind`; surface that as 400 even though we
        # already pre-checked, so we stay safe if SUPPORTED_KINDS drifts.
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        state.log.exception(
            "octowright.http.session_launch_failed",
            kind=kind,
            url=launch_kwargs["url"],
        )
        return JSONResponse({"error": f"launch failed: {e}"}, status_code=500)

    summary = _live_summary_from_launch(result)
    state.log.info(
        "octowright.http.session_launched",
        instance_id=result["instance_id"],
        kind=result["kind"],
        url=result.get("url"),
        record_video=launch_kwargs["record_video"],
        trace=launch_kwargs["trace"],
    )
    return JSONResponse(summary, status_code=201)


async def session_close(request: Request) -> JSONResponse:
    """DELETE /api/sessions/{id} — close a live session.

    Closed sessions on disk cannot be re-closed; returns 404 in that case so
    callers can distinguish "I closed something" from "nothing to do".
    """
    sid = request.path_params["id"]
    pool = _state.pool
    if sid not in pool._sessions:
        return JSONResponse(
            {"error": f"no live session with id {sid!r}; closed sessions cannot be re-closed"},
            status_code=404,
        )
    try:
        result = await pool.close(sid)
    except Exception as e:
        state.log.exception("octowright.http.session_close_failed", instance_id=sid)
        return JSONResponse({"error": f"close failed: {e}"}, status_code=500)
    body = {"closed": True, "instance_id": sid, **result}
    state.log.info("octowright.http.session_closed", instance_id=sid)
    return JSONResponse(body)


async def session_navigate(request: Request) -> JSONResponse:
    """POST /api/sessions/{id}/navigate — drive the live session's page to ``url``."""
    sid = request.path_params["id"]
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        return JSONResponse({"error": "url is required and must be a non-empty string"}, status_code=400)

    pool = _state.pool
    if sid not in pool._sessions:
        return JSONResponse(
            {"error": f"no live session with id {sid!r}"},
            status_code=404,
        )
    session = pool._sessions[sid]
    try:
        await session.navigate(url)
    except Exception as e:
        state.log.exception("octowright.http.session_navigate_failed", instance_id=sid, url=url)
        return JSONResponse({"error": f"navigate failed: {e}"}, status_code=500)
    state.log.info("octowright.http.session_navigated", instance_id=sid, url=url)
    return JSONResponse({"ok": True, "url": url})


async def recording_delete(request: Request) -> JSONResponse:
    """DELETE /api/sessions/{id}/recording — remove a closed session's files from disk."""
    sid = request.path_params["id"]
    pool = _state.pool
    if sid in pool._sessions:
        return JSONResponse(
            {"error": f"session {sid!r} is still live; close it first"},
            status_code=409,
        )

    jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
    if jsonl is None:
        return JSONResponse({"error": f"no recording found for session {sid!r}"}, status_code=404)

    deleted: list[str] = []
    stem = jsonl.stem
    for f in jsonl.parent.iterdir():
        if f.name.startswith(stem):
            try:
                f.unlink()
                deleted.append(f.name)
            except OSError as e:
                state.log.warning("recording_delete.unlink_failed", file=str(f), error=str(e))

    state.log.info("recording_deleted", session_id=sid, files=len(deleted))
    return JSONResponse({"deleted": True, "session_id": sid, "files_removed": len(deleted)})


def routes() -> list[Route]:
    return [
        Route("/api/sessions", list_sessions, methods=["GET"]),
        Route("/api/sessions", session_launch, methods=["POST"]),
        Route("/api/sessions/{id}", session_detail, methods=["GET"]),
        Route("/api/sessions/{id}/recording", recording_delete, methods=["DELETE"]),
        Route("/api/sessions/{id}", session_close, methods=["DELETE"]),
        Route("/api/sessions/{id}/navigate", session_navigate, methods=["POST"]),
    ]
