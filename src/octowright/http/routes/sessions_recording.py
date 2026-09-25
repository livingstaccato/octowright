# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Closed-session recording endpoints: delete on-disk artifacts / relaunch from a recording."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import octowright.http.state as state
from octowright.browser_pool.options import LaunchOptions
from octowright.dashboard_events import publish_dashboard_invalidation
from octowright.http.discovery import _find_recording_for, _live_summary_from_launch, _read_first_launch
from octowright.http.exposure import guard_sensitive_http
from octowright.http.recording_sidecars import (
    is_failure_dump,
    is_recording_sidecar,
    remove_contained_dir,
    session_artifact_dirs,
)


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

    removed, files_removed, dirs_removed = _remove_session_artifacts(sid, jsonl, state.RECORDINGS_DIR)
    state.log.info("recording_deleted", session_id=sid, files=files_removed, dirs=dirs_removed)
    await publish_dashboard_invalidation("sessions")
    return JSONResponse(
        {
            "deleted": True,
            "session_id": sid,
            "files_removed": files_removed,
            "dirs_removed": dirs_removed,
            # Relative to the recordings root, so the answer names what went
            # without handing the dashboard an absolute path.
            "removed": removed,
        }
    )


def _remove_session_artifacts(sid: str, jsonl: Path, root: Path) -> tuple[list[str], int, int]:
    """Every artefact the session wrote, removed: ``(removed, files, dirs)``.

    Only the JSONL's stem-named sidecars went before, and the response still
    said ``deleted: True`` while the video, downloads, frame cache and failure
    dumps -- the parts most likely to hold what someone wanted gone -- stayed.
    Each path comes from a producer's naming convention and is contained under
    the recordings root (``recording_sidecars.remove_contained_dir``). A path
    that fails to go is logged and left, like before; the rest still go.
    """
    removed: list[str] = []
    files = 0
    stem = jsonl.stem
    for f in jsonl.parent.iterdir():
        if not (is_recording_sidecar(f.name, stem) or is_failure_dump(f.name, sid)) or not f.is_file():
            continue
        try:
            f.unlink()
        except OSError as e:
            state.log.warning("recording_delete.unlink_failed", file=str(f), error=str(e))
            continue
        files += 1
        removed.append(f.name)
    dirs = 0
    for directory in session_artifact_dirs(root, sid, stem):
        try:
            if remove_contained_dir(directory, root):
                dirs += 1
                removed.append(directory.relative_to(root).as_posix())
        except OSError as e:
            state.log.warning("recording_delete.rmtree_failed", dir=str(directory), error=str(e))
    return removed, files, dirs


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
    JSONL has no parseable launch record or one the launch options refuse.
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

    try:
        # Inside the try, as sessions.py's launch route does: LaunchOptions
        # validation raises InvalidRequestError (a ValueError) for a record it
        # refuses, and above the try that escaped every handler -- Starlette is
        # built with no exception_handlers -- as a bare 500 with the offending
        # field nowhere in the body. A record the options refuse, like one
        # with no launch row, is unprocessable (422), not a server fault.
        launch_kwargs = _relaunch_kwargs_from_record(launch)
        result = await pool.launch(**launch_kwargs)
    except ValueError as e:
        return JSONResponse({"error": f"recording for {sid!r} cannot be relaunched: {e}"}, status_code=422)
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
