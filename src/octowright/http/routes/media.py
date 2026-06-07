# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Media endpoints: frame / video / trace / screenshots / trace-viewer spawn."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from octowright.http import state
from octowright.http.discovery import (
    _find_recording_for,
    _live_session_or_none,
    _resolve_artifact_path,
    _resolve_log_path,
)
from octowright.http.exposure import guard_sensitive_http
from octowright.http.routes._common import _parse_bool

# Production session ids are ``uuid.uuid4().hex[:12]`` (12 lower-case hex
# chars), but tests and a few other call sites use longer alphanumeric ids.
# Restrict to a generous-but-safe character set so a glob metachar (``*``,
# ``?``, ``[``) or path separator can't widen the result set when ``sid``
# flows into ``glob()`` patterns or filesystem joins.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _valid_session_id(sid: str) -> bool:
    return bool(_SESSION_ID_RE.match(sid))


def _frame_cache_path(session_id: str, t: float) -> Path:
    cache_dir = state.RECORDINGS_DIR / ".frame-cache" / session_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{t:.3f}.png"


async def session_frame(request: Request) -> Response:
    sid = request.path_params["id"]
    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    raw_t = request.query_params.get("t", "0")
    try:
        t = float(raw_t)
    except ValueError:
        return JSONResponse({"error": f"invalid t={raw_t!r}, must be float"}, status_code=400)

    video_path = _resolve_artifact_path(sid, "video_path")
    if video_path is None or not video_path.exists():
        return JSONResponse(
            {"error": "no video recorded for this session"},
            status_code=404,
        )

    cached = _frame_cache_path(sid, t)
    if not cached.exists():
        # Run ffmpeg in a thread — extract_frames is sync subprocess, blocks the loop.
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: state._video.extract_frames(video_path, cached.parent, at_times=[t]),
            )
        except Exception as e:
            return JSONResponse(
                {"error": f"frame extraction failed: {e}"},
                status_code=500,
            )
        # `extract_frames` writes `frame-000-t<t>.png`; rename to the cache key.
        produced = cached.parent / f"frame-000-t{t:.3f}.png"
        if produced.exists() and produced != cached:
            produced.replace(cached)
        elif not cached.exists():
            return JSONResponse(
                {"error": "frame extraction produced no file"},
                status_code=500,
            )

    # FileResponse streams from disk via sendfile() instead of reading the
    # full PNG into Python memory each request — frame scrubbing in the
    # debugger timeline pulls these in tight loops, so the allocation
    # difference adds up.
    return FileResponse(path=str(cached), media_type="image/png")


async def session_video(request: Request) -> Response:
    sid = request.path_params["id"]
    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    video_path = _resolve_artifact_path(sid, "video_path")
    if video_path is None or not video_path.exists():
        return JSONResponse(
            {"error": "no video recorded for this session"},
            status_code=404,
        )
    # Starlette's FileResponse handles HTTP Range automatically.
    return FileResponse(path=str(video_path), media_type="video/webm", filename=video_path.name)


async def session_trace(request: Request) -> Response:
    sid = request.path_params["id"]
    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    trace_path = _resolve_artifact_path(sid, "trace_path")
    if trace_path is None or not trace_path.exists():
        return JSONResponse(
            {"error": "no trace recorded for this session"},
            status_code=404,
        )
    return FileResponse(
        path=str(trace_path),
        media_type="application/zip",
        filename=trace_path.name,
    )


def _screenshot_dir_for(session_id: str) -> Path | None:
    """Look for screenshots next to the recording (matches `browser_screenshot`).

    Convention: screenshots land in the recording's parent directory with a
    filename starting with the session id.
    """
    log_path = _resolve_log_path(session_id)
    if log_path is None:
        return None
    return log_path.parent


async def session_screenshot_now(request: Request) -> Response:
    """GET /api/sessions/{id}/screenshot/now — live snapshot of the page right now.

    Live session: call ``session.page.screenshot()`` and return the raw bytes.
    Closed session: 404 (no live page to capture). Unknown id: 404.
    """
    sid = request.path_params["id"]

    fmt = request.query_params.get("format", "png").lower()
    if fmt not in {"png", "jpeg"}:
        return JSONResponse(
            {"error": "format must be 'png' or 'jpeg'"},
            status_code=400,
        )

    raw_quality = request.query_params.get("quality", "80")
    try:
        quality = int(raw_quality)
    except ValueError:
        return JSONResponse(
            {"error": f"invalid quality={raw_quality!r}, must be int 1-100"},
            status_code=400,
        )
    if not 1 <= quality <= 100:
        return JSONResponse(
            {"error": "quality must be between 1 and 100"},
            status_code=400,
        )

    raw_full = request.query_params.get("full_page", "false")
    full_page = _parse_bool(raw_full)
    if full_page is None:
        return JSONResponse(
            {"error": f"invalid full_page={raw_full!r}, must be bool"},
            status_code=400,
        )

    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    live = _live_session_or_none(sid)
    if live is None:
        # Distinguish "closed session" from "no such session" so the frontend
        # can render a helpful placeholder for the former.
        jsonl = _find_recording_for(sid, state.RECORDINGS_DIR)
        if jsonl is not None:
            return JSONResponse(
                {"error": "session is closed; live screenshot only available while the browser is running"},
                status_code=404,
            )
        return JSONResponse(
            {"error": f"no session with id {sid!r}"},
            status_code=404,
        )

    page = live.page
    kwargs: dict[str, Any] = {"type": fmt, "full_page": full_page}
    if fmt == "jpeg":
        kwargs["quality"] = quality
    try:
        data = await page.screenshot(**kwargs)
    except Exception as e:
        state.log.warning(
            "octowright.http.live_screenshot_failed",
            session_id=sid,
            error=str(e),
        )
        return JSONResponse(
            {"error": f"live screenshot failed: {e}"},
            status_code=503,
        )

    media_type = "image/jpeg" if fmt == "jpeg" else "image/png"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


async def session_screenshots(request: Request) -> JSONResponse:
    sid = request.path_params["id"]
    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    sdir = _screenshot_dir_for(sid)
    if sdir is None:
        return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
    # Defence-in-depth: sdir must resolve inside RECORDINGS_DIR. _screenshot_dir_for
    # returns the unresolved parent; if the recording dir is itself a symlink to
    # somewhere unexpected we don't want to enumerate it.
    try:
        sdir_resolved = sdir.resolve()
        sdir_resolved.relative_to(state.RECORDINGS_DIR.resolve())
    except (OSError, ValueError):
        return JSONResponse({"error": "invalid session dir"}, status_code=400)
    if not sdir_resolved.exists():
        return JSONResponse({"screenshots": []})
    out: list[dict[str, Any]] = []
    for png in sorted(sdir_resolved.glob(f"*{sid}*.png")):
        st = png.stat()
        out.append(
            {
                "path": str(png),
                "filename": png.name,
                "ts": st.st_mtime,
                "size_bytes": st.st_size,
            }
        )
    return JSONResponse({"screenshots": out})


async def session_screenshot_file(request: Request) -> Response:
    sid = request.path_params["id"]
    filename = request.path_params["filename"]
    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    sdir = _screenshot_dir_for(sid)
    if sdir is None:
        return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
    target = sdir / filename
    # Defence-in-depth: the resolved file AND its parent dir must both live
    # under RECORDINGS_DIR. Symlink-resolving only the file isn't enough — a
    # session dir that's itself a symlink could redirect the whole containment
    # anchor outside the recordings root.
    try:
        resolved = target.resolve()
        sdir_resolved = sdir.resolve()
        recordings_resolved = state.RECORDINGS_DIR.resolve()
        sdir_resolved.relative_to(recordings_resolved)
        resolved.relative_to(sdir_resolved)
    except (OSError, ValueError):
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    if not resolved.exists() or not resolved.is_file():
        return JSONResponse({"error": f"no screenshot {filename!r}"}, status_code=404)
    # Pass the already-resolved path to FileResponse so a symlink swap
    # between our containment check and FileResponse's open() can't
    # redirect to a file outside the recordings root.
    return FileResponse(path=str(resolved), media_type="image/png", filename=filename)


async def session_markdown(request: Request) -> Response:
    sid = request.path_params["id"]
    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    live = _live_session_or_none(sid)

    if live is not None:
        markdown_path = _resolve_artifact_path(sid, "markdown_path")
        if markdown_path is None:
            # Opportunistically create the cache for live sessions if it hasn't
            # been generated yet (for first request after launch).
            try:
                await live.capture_markdown()
            except Exception as exc:
                return JSONResponse(
                    {"error": f"could not generate markdown: {exc!r}"},
                    status_code=500,
                )
            markdown_path = _resolve_artifact_path(sid, "markdown_path")
    else:
        markdown_path = _resolve_artifact_path(sid, "markdown_path")

    if markdown_path is None or not markdown_path.exists():
        return JSONResponse(
            {"error": "no markdown cache available for this session"},
            status_code=404,
        )
    return Response(content=markdown_path.read_text(encoding="utf-8"), media_type="text/markdown")


async def trace_open(request: Request) -> JSONResponse:
    """POST /api/sessions/{id}/trace/open — same payload as ``browser_open_trace``."""
    sid = request.path_params["id"]
    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    trace_path = _resolve_artifact_path(sid, "trace_path")
    if trace_path is None or not trace_path.exists():
        return JSONResponse(
            {"error": "no trace recorded for this session"},
            status_code=404,
        )
    if state.shutil.which("npx") is None:
        return JSONResponse(
            {"error": "npx not on PATH; install Node.js + Playwright"},
            status_code=500,
        )
    proc = state.subprocess.Popen(
        ["npx", "playwright", "show-trace", str(trace_path)],
        stdout=state.subprocess.DEVNULL,
        stderr=state.subprocess.DEVNULL,
        start_new_session=True,
    )
    state.log.info("octowright.http.trace_opened", session_id=sid, pid=proc.pid)
    return JSONResponse({"pid": proc.pid, "trace_path": str(trace_path)})


def routes() -> list[Route]:
    return [
        Route(
            "/api/sessions/{id}/frame",
            guard_sensitive_http(session_frame, side_effect_get=True),
            methods=["GET"],
        ),
        Route("/api/sessions/{id}/video", guard_sensitive_http(session_video), methods=["GET"]),
        Route("/api/sessions/{id}/trace", guard_sensitive_http(session_trace), methods=["GET"]),
        Route(
            "/api/sessions/{id}/markdown",
            guard_sensitive_http(session_markdown, side_effect_get=True),
            methods=["GET"],
        ),
        Route("/api/sessions/{id}/trace/open", guard_sensitive_http(trace_open), methods=["POST"]),
        Route(
            "/api/sessions/{id}/screenshot/now",
            guard_sensitive_http(session_screenshot_now, side_effect_get=True),
            methods=["GET"],
        ),
        Route("/api/sessions/{id}/screenshots", guard_sensitive_http(session_screenshots), methods=["GET"]),
        Route(
            "/api/sessions/{id}/screenshots/{filename}",
            guard_sensitive_http(session_screenshot_file),
            methods=["GET"],
        ),
    ]
