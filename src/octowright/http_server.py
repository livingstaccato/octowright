# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""HTTP debugger sidecar.

A Starlette application that exposes the live `BrowserPool`/`ScenarioPool`
state plus on-disk recordings/screenshots/videos/traces to a single-page
frontend. Designed to run alongside the MCP stdio server in the same event
loop (see `cli.py serve`).

Endpoints (mirror the API contract in MCP-SHARED-CONTRACT.md):
    GET  /                                         → static index.html
    GET  /sessions/{id}                            → static session.html
    GET  /api/sessions                             → live + closed session lists
    GET  /api/sessions/{id}                        → SessionDetail
    GET  /api/sessions/{id}/events?since=N         → tail JSONL events
    GET  /api/sessions/{id}/console?level=&since=N → console messages (paginated)
    GET  /api/sessions/{id}/downloads?since=N      → downloads (paginated)
    WS   /api/sessions/{id}/tail                   → push events ~1Hz (LIVE only;
                                                     closed/unknown → immediate close)
    GET  /api/sessions/{id}/frame?t=<sec>          → ffmpeg-extracted PNG (cached)
    GET  /api/sessions/{id}/video                  → video bytes (range supported)
    GET  /api/sessions/{id}/trace                  → trace .zip
    GET  /api/sessions/{id}/screenshots            → list screenshots
    GET  /api/sessions/{id}/screenshots/{file}     → screenshot PNG bytes
    POST /api/sessions/{id}/trace/open             → spawn `npx playwright show-trace`
    GET  /api/scenarios                            → live scenarios
    GET  /api/personas                             → persona summaries
    GET  /api/macros                               → macro summaries
    GET  /api/health                               → liveness probe

State is read straight off the shared singletons in
`octowright.server._state` (the same `pool` and `scenario_pool` the MCP tools
mutate); closed sessions are reconstructed from `RECORDINGS_DIR/*.jsonl`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger
from starlette.applications import Starlette
from starlette.endpoints import WebSocketEndpoint
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import macros as _macros
from . import personas as _personas
from . import video as _video
from .defaults import HTTP_HOST, HTTP_PORT, HTTP_PORT_RETRIES, RECORDINGS_DIR
from .server import _state

log = get_logger(__name__)

# Frontend bundle lives here (sibling subagent populates the directory).
FRONTEND_DIR = Path(__file__).parent / "server" / "frontend"

# Polling interval for the WS /tail endpoint. ~1 Hz feels live without
# hammering the file system.
TAIL_POLL_SECONDS = 1.0


# ---------------------------------------------------------------------------
# Module-level state used by the dashboard MCP tool to discover the bound port.
# Populated by `serve_app()` once uvicorn binds; cleared on shutdown.
# ---------------------------------------------------------------------------

_RUNTIME_HOST: str | None = None
_RUNTIME_PORT: int | None = None
_RUNTIME_ERROR: str | None = None


def runtime_url() -> str | None:
    """Return the dashboard URL the HTTP server is bound to, or None if not running."""
    if _RUNTIME_HOST is None or _RUNTIME_PORT is None:
        return None
    return f"http://{_RUNTIME_HOST}:{_RUNTIME_PORT}/"


def runtime_session_url(session_id: str) -> str | None:
    base = runtime_url()
    if base is None:
        return None
    return f"{base}sessions/{session_id}"


def runtime_status() -> dict[str, Any]:
    """Snapshot used by the `octowright_dashboard_url` MCP tool."""
    return {
        "running": _RUNTIME_HOST is not None and _RUNTIME_PORT is not None,
        "host": _RUNTIME_HOST,
        "port": _RUNTIME_PORT,
        "error": _RUNTIME_ERROR,
    }


# ---------------------------------------------------------------------------
# Closed-session discovery
# ---------------------------------------------------------------------------


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def _read_first_launch(jsonl_path: Path) -> dict[str, Any] | None:
    """Find the first `launch` event in a JSONL recording (cheap scan)."""
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
                if entry.get("action") == "launch":
                    return entry
    except OSError:
        return None
    return None


def _iter_recordings(recordings_dir: Path) -> list[Path]:
    if not recordings_dir.exists():
        return []
    return sorted(recordings_dir.glob("*.jsonl"))


def _instance_id_from_recording_name(stem: str) -> str | None:
    """Filename layout: ``<stamp>-<kind>-<instance_id>[-<label>]``.

    instance_id is the third dash-separated token. Returns None if the stem
    doesn't have at least three parts.
    """
    parts = stem.split("-")
    if len(parts) < 3:
        return None
    return parts[2]


def _summarise_recording(jsonl_path: Path) -> dict[str, Any] | None:
    """Build a SessionSummary for a closed-session JSONL on disk."""
    instance_id = _instance_id_from_recording_name(jsonl_path.stem)
    if instance_id is None:
        return None
    launch = _read_first_launch(jsonl_path) or {}
    stat = jsonl_path.stat()
    started = launch.get("ts") or _iso(stat.st_ctime)
    return {
        "id": instance_id,
        "kind": launch.get("kind") or "unknown",
        "label": launch.get("label"),
        "profile": launch.get("profile"),
        "url": launch.get("url"),
        "started_at": started,
        "live": False,
        "log_path": str(jsonl_path),
    }


def _live_summary(session: Any) -> dict[str, Any]:
    return {
        "id": session.instance_id,
        "kind": session.kind,
        "label": session.label,
        "profile": session.profile,
        "url": session.url,
        "started_at": _iso(Path(session.log_path).stat().st_ctime)
        if Path(session.log_path).exists()
        else _iso(time.time()),
        "live": True,
        "log_path": str(session.log_path),
    }


def _closed_sessions(recordings_dir: Path, live_log_paths: set[str]) -> list[dict[str, Any]]:
    """Every JSONL file whose path is not currently held by a live session."""
    out: list[dict[str, Any]] = []
    for jsonl in _iter_recordings(recordings_dir):
        if str(jsonl) in live_log_paths:
            continue
        summary = _summarise_recording(jsonl)
        if summary is not None:
            out.append(summary)
    # Most-recent first — matches the dashboard's expected ordering.
    out.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    return out


def _scan_recording_artefacts(jsonl_path: Path) -> dict[str, Any]:
    """Walk a recording's sibling files for video / trace / counts."""
    counts = {"action_count": 0, "console_count": 0, "download_count": 0, "page_count": 1}
    title: str | None = None
    last_url: str | None = None
    video_path: str | None = None
    trace_path: str | None = None

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
                counts["action_count"] += 1
                action = entry.get("action")
                if action == "console":
                    counts["console_count"] += 1
                elif action == "download_saved":
                    counts["download_count"] += 1
                elif action == "popup_opened":
                    counts["page_count"] += 1
                if action == "navigate" and entry.get("url"):
                    last_url = entry["url"]
                if action == "close":
                    if entry.get("video_path"):
                        video_path = entry["video_path"]
                    if entry.get("trace_path"):
                        trace_path = entry["trace_path"]
    except OSError:
        pass

    # Trace file convention: <log>.trace.zip (set by `BrowserSession.close()`).
    candidate_trace = jsonl_path.with_suffix(".trace.zip")
    if trace_path is None and candidate_trace.exists():
        trace_path = str(candidate_trace)
    return {
        **counts,
        "title": title,
        "video_path": video_path,
        "trace_path": trace_path,
        "url": last_url,
    }


# ---------------------------------------------------------------------------
# Session lookups (live OR on-disk recording)
# ---------------------------------------------------------------------------


def _find_recording_for(session_id: str, recordings_dir: Path) -> Path | None:
    if not recordings_dir.exists():
        return None
    for jsonl in _iter_recordings(recordings_dir):
        if _instance_id_from_recording_name(jsonl.stem) == session_id:
            return jsonl
    return None


def _live_session_or_none(session_id: str) -> Any | None:
    pool = _state.pool
    sessions = pool._sessions
    return sessions.get(session_id)


def _resolve_log_path(session_id: str) -> Path | None:
    live = _live_session_or_none(session_id)
    if live is not None:
        return Path(live.log_path)
    return _find_recording_for(session_id, RECORDINGS_DIR)


def _resolve_video_path(session_id: str) -> Path | None:
    live = _live_session_or_none(session_id)
    if live is not None and live.video_path is not None:
        return Path(live.video_path)
    jsonl = _find_recording_for(session_id, RECORDINGS_DIR)
    if jsonl is None:
        return None
    artefacts = _scan_recording_artefacts(jsonl)
    if artefacts["video_path"]:
        return Path(artefacts["video_path"])
    return None


def _resolve_trace_path(session_id: str) -> Path | None:
    live = _live_session_or_none(session_id)
    if live is not None and live.trace_path is not None:
        return Path(live.trace_path)
    jsonl = _find_recording_for(session_id, RECORDINGS_DIR)
    if jsonl is None:
        return None
    artefacts = _scan_recording_artefacts(jsonl)
    if artefacts["trace_path"]:
        return Path(artefacts["trace_path"])
    return None


# ---------------------------------------------------------------------------
# JSONL tail (matches `browser_tail_recording` semantics so the WS payloads
# look identical to the existing MCP tool — the frontend can speak one shape).
# ---------------------------------------------------------------------------


def _tail_jsonl(log_path: Path, since: int) -> dict[str, Any]:
    if not log_path.exists():
        return {"events": [], "cursor": since, "total_bytes": 0, "complete": True}
    with log_path.open("rb") as fh:
        fh.seek(since)
        data = fh.read()
    total_bytes = log_path.stat().st_size
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if text.endswith("\n"):
        complete_lines = [ln for ln in lines if ln.strip()]
        partial_bytes = 0
    else:
        complete_lines = [ln for ln in lines[:-1] if ln.strip()]
        partial_bytes = len(lines[-1].encode("utf-8"))
    new_cursor = since + len(data) - partial_bytes
    events: list[dict[str, Any]] = []
    for raw in complete_lines:
        try:
            events.append(json.loads(raw.strip()))
        except json.JSONDecodeError:
            continue
    return {
        "events": events,
        "cursor": new_cursor,
        "total_bytes": total_bytes,
        "complete": new_cursor == total_bytes,
    }


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------


async def health_endpoint(_request: Request) -> JSONResponse:
    # Version pulled from package metadata at request time so a `pip install
    # --upgrade` is reflected without a server restart.
    try:
        from importlib.metadata import version

        ver = version("octowright")
    except Exception:
        ver = "unknown"
    return JSONResponse({"ok": True, "version": ver})


async def list_sessions(_request: Request) -> JSONResponse:
    pool = _state.pool
    live = [_live_summary(s) for s in pool._sessions.values()]
    live_paths = {s["log_path"] for s in live}
    closed = _closed_sessions(RECORDINGS_DIR, live_paths)
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

    jsonl = _find_recording_for(sid, RECORDINGS_DIR)
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


def _paginate(items: list[dict[str, Any]], since: int) -> tuple[list[dict[str, Any]], int, int]:
    """Slice ``items[since:]`` and return (slice, next_cursor, total).

    Negative or out-of-range ``since`` is clamped into [0, total].
    """
    total = len(items)
    if since < 0:
        since = 0
    if since > total:
        since = total
    return items[since:], total, total


def _read_console_from_jsonl(jsonl_path: Path) -> list[dict[str, Any]]:
    """Reconstruct console messages from a JSONL recording.

    NOTE: as of this writing ``BrowserSession.attach_console`` does NOT persist
    console messages to the JSONL log — they live only on the in-memory
    ``session.console`` list. So for closed sessions this returns ``[]``. The
    scan is left in place so the endpoint Just Works once a future change starts
    recording an ``action: "console"`` row alongside ``download_saved`` etc.
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
                if entry.get("action") != "console":
                    continue
                out.append(
                    {
                        "level": entry.get("level"),
                        "text": entry.get("text", ""),
                        "page_index": entry.get("page_index"),
                    }
                )
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


def _parse_since(request: Request) -> tuple[int | None, JSONResponse | None]:
    """Parse the ``since`` query param. Returns (since, error_response_or_None)."""
    raw = request.query_params.get("since")
    if raw is None:
        return 0, None
    try:
        return int(raw), None
    except ValueError:
        return None, JSONResponse(
            {"error": f"invalid since={raw!r}, must be int"},
            status_code=400,
        )


async def session_console(request: Request) -> JSONResponse:
    """Return paginated console messages for a session.

    Live sessions read straight from ``pool.get(id).console``. Closed sessions
    scan the JSONL recording for ``action: "console"`` rows (today this yields
    an empty list because attach_console doesn't persist — see
    ``_read_console_from_jsonl``). Optional ``level=`` filters by log level
    (case-sensitive). Optional ``since=`` is a 0-based index; the response's
    ``cursor`` is always the new total so callers can pass it on the next poll.

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
        jsonl = _find_recording_for(sid, RECORDINGS_DIR)
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
        jsonl = _find_recording_for(sid, RECORDINGS_DIR)
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


def _frame_cache_path(session_id: str, t: float) -> Path:
    cache_dir = RECORDINGS_DIR / ".frame-cache" / session_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{t:.3f}.png"


async def session_frame(request: Request) -> Response:
    sid = request.path_params["id"]
    raw_t = request.query_params.get("t", "0")
    try:
        t = float(raw_t)
    except ValueError:
        return JSONResponse({"error": f"invalid t={raw_t!r}, must be float"}, status_code=400)

    video_path = _resolve_video_path(sid)
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
                lambda: _video.extract_frames(video_path, cached.parent, at_times=[t]),
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

    return Response(content=cached.read_bytes(), media_type="image/png")


async def session_video(request: Request) -> Response:
    sid = request.path_params["id"]
    video_path = _resolve_video_path(sid)
    if video_path is None or not video_path.exists():
        return JSONResponse(
            {"error": "no video recorded for this session"},
            status_code=404,
        )
    # Starlette's FileResponse handles HTTP Range automatically.
    return FileResponse(path=str(video_path), media_type="video/webm", filename=video_path.name)


async def session_trace(request: Request) -> Response:
    sid = request.path_params["id"]
    trace_path = _resolve_trace_path(sid)
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


async def session_screenshots(request: Request) -> JSONResponse:
    sid = request.path_params["id"]
    sdir = _screenshot_dir_for(sid)
    if sdir is None:
        return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
    if not sdir.exists():
        return JSONResponse({"screenshots": []})
    out: list[dict[str, Any]] = []
    for png in sorted(sdir.glob(f"*{sid}*.png")):
        stat = png.stat()
        out.append(
            {
                "path": str(png),
                "filename": png.name,
                "ts": stat.st_mtime,
                "size_bytes": stat.st_size,
            }
        )
    return JSONResponse({"screenshots": out})


async def session_screenshot_file(request: Request) -> Response:
    sid = request.path_params["id"]
    filename = request.path_params["filename"]
    sdir = _screenshot_dir_for(sid)
    if sdir is None:
        return JSONResponse({"error": f"no session with id {sid!r}"}, status_code=404)
    target = sdir / filename
    # Defence-in-depth: filename must resolve inside `sdir` (no `../` escapes).
    try:
        resolved = target.resolve()
        sdir_resolved = sdir.resolve()
        resolved.relative_to(sdir_resolved)
    except (OSError, ValueError):
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": f"no screenshot {filename!r}"}, status_code=404)
    return FileResponse(path=str(target), media_type="image/png", filename=filename)


async def list_scenarios(_request: Request) -> JSONResponse:
    spool = _state.scenario_pool
    return JSONResponse({"live": spool.list_live()})


async def list_personas_endpoint(_request: Request) -> JSONResponse:
    rows = _personas.list_personas()
    out = [
        {
            "name": r["name"],
            "display_name": r.get("display_name"),
            "engines": r.get("engines", []),
            "last_used": r.get("last_used"),
        }
        for r in rows
    ]
    return JSONResponse(out)


async def list_macros_endpoint(_request: Request) -> JSONResponse:
    rows = _macros.list_macros()
    out = [
        {
            "name": r["name"],
            "description": r.get("description"),
            "parameters": r.get("parameters", []),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]
    return JSONResponse(out)


async def trace_open(request: Request) -> JSONResponse:
    """POST /api/sessions/{id}/trace/open — same payload as ``browser_open_trace``."""
    sid = request.path_params["id"]
    trace_path = _resolve_trace_path(sid)
    if trace_path is None or not trace_path.exists():
        return JSONResponse(
            {"error": "no trace recorded for this session"},
            status_code=404,
        )
    if shutil.which("npx") is None:
        return JSONResponse(
            {"error": "npx not on PATH; install Node.js + Playwright"},
            status_code=500,
        )
    proc = subprocess.Popen(
        ["npx", "playwright", "show-trace", str(trace_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log.info("octowright.http.trace_opened", session_id=sid, pid=proc.pid)
    return JSONResponse({"pid": proc.pid, "trace_path": str(trace_path)})


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
        await websocket.accept()
        sid = websocket.path_params["id"]
        live_session = _live_session_or_none(sid)
        if live_session is None:
            # Either a closed session (recording present) or unknown.
            jsonl = _find_recording_for(sid, RECORDINGS_DIR)
            if jsonl is not None:
                await websocket.close(
                    code=1003,
                    reason="closed sessions don't support tail; use GET /api/sessions/{id}/events instead",
                )
            else:
                await websocket.close(code=1008, reason=f"no session with id {sid}")
            return

        log_path = Path(live_session.log_path)
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
                await asyncio.sleep(TAIL_POLL_SECONDS)
        except WebSocketDisconnect:
            return


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _frontend_routes() -> list[Mount]:
    """Mount the bundled SPA at `/` if the frontend bundle is present.

    The bundle is produced by the sibling TS subagent. When it isn't there
    yet (first-run, dev), the API still works — the dashboard is just blank.
    """
    if FRONTEND_DIR.exists() and FRONTEND_DIR.is_dir():
        return [Mount("/", app=StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")]
    return []


def build_app() -> Starlette:
    """Build the Starlette ASGI app. Stateless — safe to call from tests."""
    routes: list[Any] = [
        Route("/api/health", health_endpoint, methods=["GET"]),
        Route("/api/sessions", list_sessions, methods=["GET"]),
        Route("/api/sessions/{id}", session_detail, methods=["GET"]),
        Route("/api/sessions/{id}/events", session_events, methods=["GET"]),
        Route("/api/sessions/{id}/console", session_console, methods=["GET"]),
        Route("/api/sessions/{id}/downloads", session_downloads, methods=["GET"]),
        Route("/api/sessions/{id}/frame", session_frame, methods=["GET"]),
        Route("/api/sessions/{id}/video", session_video, methods=["GET"]),
        Route("/api/sessions/{id}/trace", session_trace, methods=["GET"]),
        Route("/api/sessions/{id}/trace/open", trace_open, methods=["POST"]),
        Route("/api/sessions/{id}/screenshots", session_screenshots, methods=["GET"]),
        Route(
            "/api/sessions/{id}/screenshots/{filename}",
            session_screenshot_file,
            methods=["GET"],
        ),
        Route("/api/scenarios", list_scenarios, methods=["GET"]),
        Route("/api/personas", list_personas_endpoint, methods=["GET"]),
        Route("/api/macros", list_macros_endpoint, methods=["GET"]),
        WebSocketRoute("/api/sessions/{id}/tail", TailEndpoint),
    ]
    routes.extend(_frontend_routes())
    return Starlette(routes=routes)


# ---------------------------------------------------------------------------
# Lifecycle: bind + serve in the same event loop as MCP
# ---------------------------------------------------------------------------


def _port_is_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pick_port(host: str, preferred: int, retries: int) -> int | None:
    """Try preferred port; fall back to next ``retries`` ports. Returns None on failure."""
    for offset in range(retries + 1):
        candidate = preferred + offset
        if _port_is_free(host, candidate):
            return candidate
    return None


async def serve_app(
    *,
    host: str = HTTP_HOST,
    port: int = HTTP_PORT,
    retries: int = HTTP_PORT_RETRIES,
) -> None:
    """Run uvicorn in the current event loop until cancelled.

    Designed for `asyncio.gather(mcp_task, http_task)` in `cli.py serve`. If
    the preferred port is busy, walks up to ``retries`` ports before giving
    up. On total failure, logs and returns — the MCP server keeps running.
    """
    global _RUNTIME_HOST, _RUNTIME_PORT, _RUNTIME_ERROR

    bound = _pick_port(host, port, retries)
    if bound is None:
        _RUNTIME_ERROR = f"port {port} (and {retries} fallbacks) all in use; HTTP debugger disabled"
        log.warning("octowright.http.bind_failed", host=host, preferred=port, retries=retries)
        return

    import uvicorn

    config = uvicorn.Config(
        app=build_app(),
        host=host,
        port=bound,
        log_level="warning",
        access_log=False,
        # Reuse the running loop — this is the whole point of the sidecar.
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    _RUNTIME_HOST = host
    _RUNTIME_PORT = bound
    _RUNTIME_ERROR = None
    log.info("octowright.http.listening", host=host, port=bound)
    try:
        await server.serve()
    finally:
        _RUNTIME_HOST = None
        _RUNTIME_PORT = None
        log.info("octowright.http.stopped", host=host, port=bound)
