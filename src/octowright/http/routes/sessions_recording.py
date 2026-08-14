# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Closed-session recording endpoints: delete on-disk artifacts / relaunch from a recording."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import octowright.http.state as state
from octowright.browser_pool.options import LaunchOptions
from octowright.dashboard_events import publish_dashboard_invalidation
from octowright.http.discovery import _find_recording_for, _live_summary_from_launch, _read_first_launch
from octowright.http.exposure import guard_sensitive_http
from octowright.http.recording_sidecars import is_recording_sidecar


async def recording_delete(request: Request) -> JSONResponse:
    """DELETE /api/sessions/{id}/recording — remove a closed session's files from disk."""
    sid = request.path_params["id"]
    pool = state.pool
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
        if is_recording_sidecar(f.name, stem):
            try:
                f.unlink()
                deleted.append(f.name)
            except OSError as e:
                state.log.warning("recording_delete.unlink_failed", file=str(f), error=str(e))

    state.log.info("recording_deleted", session_id=sid, files=len(deleted))
    await publish_dashboard_invalidation("sessions")
    return JSONResponse({"deleted": True, "session_id": sid, "files_removed": len(deleted)})


def _relaunch_kwargs_from_record(launch: dict[str, Any]) -> dict[str, Any]:
    """Translate a JSONL ``launch`` record into ``pool.launch`` kwargs.

    The JSONL-shape → LaunchOptions translation (nested viewport dict,
    ``video_dir`` → ``record_video`` bool, default ``headed=True``) lives on
    ``LaunchOptions.from_launch_record``; ``with_har_rotated`` then bumps
    the HAR sibling so the relaunch doesn't clobber the prior recording.
    """
    return LaunchOptions.from_launch_record(launch).with_har_rotated().to_pool_kwargs()


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
    pool = state.pool
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

    launch_kwargs = _relaunch_kwargs_from_record(launch)

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
        Route("/api/sessions/{id}/recording", guard_sensitive_http(recording_delete), methods=["DELETE"]),
        Route("/api/sessions/{id}/relaunch", guard_sensitive_http(session_relaunch), methods=["POST"]),
    ]
