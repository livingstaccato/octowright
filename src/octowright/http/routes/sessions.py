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

import octowright.http.state as state
import octowright.server._state as _state
from octowright.defaults import DEFAULT_URL, SUPPORTED_KINDS
from octowright.http.artifacts import _build_cache_components
from octowright.http.artifacts import cache_report_for_recording as _cache_report_for_recording
from octowright.http.dashboard_events import publish_dashboard_invalidation
from octowright.http.discovery import (
    _closed_sessions,
    _find_recording_for,
    _iso,
    _live_session_or_none,
    _live_summary,
    _read_first_launch,
    _resolve_markdown_path,
    _scan_recording_artefacts,
    _summarise_recording,
)
from octowright.http.exposure import guard_sensitive_http
from octowright.http.routes._common import _read_json_body


async def list_sessions(_request: Request) -> JSONResponse:
    pool = _state.pool
    live = [_live_summary(s) for s in pool.iter_sessions()]
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
        markdown_path: str | None = None
        live_markdown_path = _resolve_markdown_path(live.instance_id)
        if live.markdown_path is not None:
            markdown_path = str(live.markdown_path)
        elif live_markdown_path is not None:
            markdown_path = str(live_markdown_path)
        detail = {
            **_live_summary(live),
            "video_path": str(live.video_path) if live.video_path else None,
            "trace_path": str(live.trace_path) if live.trace_path else None,
            "markdown_path": markdown_path,
            "websocket_path": str(live.websocket_path) if getattr(live, "websocket_path", None) else None,
            "action_count": -1,  # unknown without re-reading the file
            "console_count": len(live.console),
            "download_count": len(live.downloads),
            "page_count": len(live.pages),
            "title": title,
            "cache": _build_cache_components(
                session_id=live.instance_id,
                jsonl_path=Path(live.log_path),
                markdown_path=Path(markdown_path)
                if markdown_path
                else (live.markdown_path if live.markdown_path else None),
                trace_path=Path(live.trace_path) if live.trace_path else None,
                video_path=Path(live.video_path) if live.video_path else None,
                websocket_path=live.websocket_path if getattr(live, "websocket_path", None) else None,
            ),
        }
        # Action count is cheap to derive from the JSONL on disk; do it once.
        log_path = Path(live.log_path)
        if log_path.exists():
            artefacts = _scan_recording_artefacts(log_path)
            detail["action_count"] = artefacts["action_count"]
            detail["event_count"] = artefacts["event_count"]

        # ARIA tree snapshot
        with contextlib.suppress(Exception):
            aria = await live.page.locator("html").aria_snapshot()
            detail["aria"] = aria

        # Macro Intent (if log exists)
        if log_path.exists():
            from octowright.macros import load_macro_from_recording
            from octowright.server.macro_semantic import get_semantic_intent

            with contextlib.suppress(Exception):
                actions = load_macro_from_recording(log_path)
                detail["macro_intent"] = get_semantic_intent(actions)

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
        "markdown_path": artefacts["markdown_path"],
        "websocket_path": artefacts["websocket_path"],
        "event_count": artefacts["event_count"],
        "action_count": artefacts["action_count"],
        "console_count": artefacts["console_count"],
        "download_count": artefacts["download_count"],
        "page_count": artefacts["page_count"],
        "title": artefacts["title"],
        "cache": _cache_report_for_recording(jsonl),
    }
    if artefacts["url"]:
        detail["url"] = artefacts["url"]

    # For closed sessions, we can still try to get the macro intent from disk
    from octowright.macros import load_macro_from_recording
    from octowright.server.macro_semantic import get_semantic_intent

    with contextlib.suppress(Exception):
        actions = load_macro_from_recording(jsonl)
        detail["macro_intent"] = get_semantic_intent(actions)

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
    await publish_dashboard_invalidation("sessions")
    return JSONResponse(summary, status_code=201)


async def session_close(request: Request) -> JSONResponse:
    """DELETE /api/sessions/{id} — close a live session.

    Closed sessions on disk cannot be re-closed; returns 404 in that case so
    callers can distinguish "I closed something" from "nothing to do".
    """
    sid = request.path_params["id"]
    pool = _state.pool
    if not pool.has_session(sid):
        return JSONResponse(
            {"error": f"no live session with id {sid!r}; closed sessions cannot be re-closed"},
            status_code=404,
        )
    try:
        result = await pool.close(sid)
    except Exception as e:
        state.log.exception("octowright.http.session_close_failed", instance_id=sid)
        return JSONResponse({"error": f"close failed: {e}"}, status_code=500)

    body: dict[str, Any] = {"closed": True, "instance_id": sid, **result}
    log_path = result.get("log_path")
    if isinstance(log_path, str) and log_path:
        try:
            body["cache"] = _cache_report_for_recording(Path(log_path))
        except Exception:
            state.log.warning(
                "octowright.http.session_close_cache_report_failed",
                instance_id=sid,
                log_path=log_path,
            )

    state.log.info("octowright.http.session_closed", instance_id=sid)
    await publish_dashboard_invalidation("sessions")
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
    if not pool.has_session(sid):
        return JSONResponse(
            {"error": f"no live session with id {sid!r}"},
            status_code=404,
        )
    session = pool.get(sid)
    try:
        await session.navigate(url)
    except Exception as e:
        state.log.exception("octowright.http.session_navigate_failed", instance_id=sid, url=url)
        return JSONResponse({"error": f"navigate failed: {e}"}, status_code=500)
    state.log.info("octowright.http.session_navigated", instance_id=sid, url=url)
    return JSONResponse({"ok": True, "url": url})


async def session_selector_validate(request: Request) -> JSONResponse:
    """POST /api/sessions/{id}/selector/validate — check a CSS selector against a live page."""
    sid = request.path_params["id"]
    payload, err = await _read_json_body(request)
    if err is not None:
        return err
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    selector = payload.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        return JSONResponse({"error": "selector is required and must be a non-empty string"}, status_code=400)

    pool = _state.pool
    if not pool.has_session(sid):
        return JSONResponse({"error": f"no live session with id {sid!r}"}, status_code=404)
    session = pool.get(sid)
    try:
        count = await session.page.locator(selector).count()
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "selector": selector,
                "found": False,
                "count": 0,
                "error": str(e),
            },
            status_code=400,
        )
    return JSONResponse({"ok": True, "selector": selector, "found": count > 0, "count": count})


async def recording_delete(request: Request) -> JSONResponse:
    """DELETE /api/sessions/{id}/recording — remove a closed session's files from disk."""
    sid = request.path_params["id"]
    pool = _state.pool
    if pool.has_session(sid):
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
    await publish_dashboard_invalidation("sessions")
    return JSONResponse({"deleted": True, "session_id": sid, "files_removed": len(deleted)})


async def session_relaunch(request: Request) -> JSONResponse:
    """POST /api/sessions/{id}/relaunch — start a fresh session with the same launch params.

    Reads the first ``launch`` record from the closed session's JSONL and
    calls ``pool.launch(...)`` with the same kind / profile / label / url /
    viewport. Returns the SessionSummary for the NEW session (new
    ``instance_id``); the old recording is untouched. Profile-backed sessions
    pick up persisted cookies / localStorage automatically.

    409 if the session is still live; 404 if no recording exists; 422 if the
    JSONL has no parseable launch record.
    """
    sid = request.path_params["id"]
    pool = _state.pool
    if pool.has_session(sid):
        return JSONResponse(
            {"error": f"session {sid!r} is still live; relaunch only applies to closed sessions"},
            status_code=409,
        )

    jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
    if jsonl is None:
        return JSONResponse({"error": f"no recording found for session {sid!r}"}, status_code=404)

    launch = _read_first_launch(jsonl)
    if launch is None:
        return JSONResponse(
            {"error": f"recording for {sid!r} has no parseable launch record"},
            status_code=422,
        )

    viewport = launch.get("viewport") if isinstance(launch.get("viewport"), dict) else None
    launch_kwargs: dict[str, Any] = {
        "kind": launch["kind"],
        "url": launch.get("url") or DEFAULT_URL,
        "label": launch.get("label"),
        "profile": launch.get("profile"),
        "viewport_w": viewport.get("w") if viewport else None,
        "viewport_h": viewport.get("h") if viewport else None,
        "headed": launch.get("headed", True),
        "stabilize": launch.get("stabilize", False),
        "record_video": bool(launch.get("video_dir")),
        "trace": launch.get("trace", False),
    }

    try:
        result = await pool.launch(**launch_kwargs)
    except Exception as e:
        state.log.exception("octowright.http.session_relaunch_failed", session_id=sid)
        return JSONResponse({"error": f"relaunch failed: {e}"}, status_code=500)

    summary = _live_summary_from_launch(result)
    state.log.info(
        "octowright.http.session_relaunched",
        original_session_id=sid,
        instance_id=result["instance_id"],
        kind=result["kind"],
    )
    await publish_dashboard_invalidation("sessions")
    return JSONResponse(summary, status_code=201)


def routes() -> list[Route]:
    return [
        Route("/api/sessions", guard_sensitive_http(list_sessions), methods=["GET"]),
        Route("/api/sessions", guard_sensitive_http(session_launch), methods=["POST"]),
        Route("/api/sessions/{id}", guard_sensitive_http(session_detail), methods=["GET"]),
        Route("/api/sessions/{id}/recording", guard_sensitive_http(recording_delete), methods=["DELETE"]),
        Route("/api/sessions/{id}/relaunch", guard_sensitive_http(session_relaunch), methods=["POST"]),
        Route("/api/sessions/{id}", guard_sensitive_http(session_close), methods=["DELETE"]),
        Route("/api/sessions/{id}/navigate", guard_sensitive_http(session_navigate), methods=["POST"]),
        Route(
            "/api/sessions/{id}/selector/validate",
            guard_sensitive_http(session_selector_validate),
            methods=["POST"],
        ),
    ]
