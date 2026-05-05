# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Closed-session discovery + JSONL scan helpers.

Closed sessions are reconstructed by walking ``state.RECORDINGS_DIR`` for
``*.jsonl`` files; the helpers here parse the filename layout, extract the
first ``launch`` event for metadata, and aggregate sibling video/trace files.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from octowright.http import state
from octowright.recorder import tail_log
from octowright.server import _state


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


def _human_bytes(size_bytes: int) -> str:
    """Compact human-readable byte size."""
    if size_bytes < 0:
        size_bytes = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return "0 B"


def _path_size(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _build_component(path: Path | None) -> dict[str, Any]:
    exists = bool(path is not None and path.exists())
    size_bytes = _path_size(path) if exists else 0
    return {
        "size_bytes": size_bytes,
        "size_human": _human_bytes(size_bytes),
        "path": str(path) if path else None,
        "exists": exists,
    }


def _find_screenshot_entries(recording_dir: Path, session_id: str | None) -> tuple[list[str], int]:
    if session_id is None:
        return [], 0
    total = 0
    entries: list[str] = []
    for path in sorted(recording_dir.glob(f"*{session_id}*.png")):
        if not path.is_file():
            continue
        entries.append(str(path))
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return entries, total


def _build_cache_components(
    *,
    session_id: str | None,
    jsonl_path: Path,
    markdown_path: Path | None = None,
    trace_path: Path | None = None,
    video_path: Path | None = None,
    websocket_path: Path | None = None,
) -> dict[str, Any]:
    screenshot_paths, screenshot_size = _find_screenshot_entries(jsonl_path.parent, session_id)
    components: dict[str, Any] = {
        "jsonl": _build_component(jsonl_path),
        "markdown": _build_component(markdown_path),
        "trace": _build_component(trace_path),
        "video": _build_component(video_path),
        "websocket": _build_component(websocket_path),
        "screenshots": {
            "size_bytes": screenshot_size,
            "size_human": _human_bytes(screenshot_size),
            "count": len(screenshot_paths),
            "paths": screenshot_paths,
        },
    }
    total_bytes = sum(component["size_bytes"] for component in components.values() if isinstance(component, dict))
    recommendations: list[str] = []
    if total_bytes > 300 * 1024 * 1024:
        recommendations.append("Session cache is large. Consider recordings_cleanup to free storage.")
    if components["websocket"]["size_bytes"] > 50 * 1024 * 1024:
        recommendations.append("Websocket cache is large. Disable websocket capture when not needed.")
    if components["video"]["size_bytes"] > 200 * 1024 * 1024:
        recommendations.append("Video capture dominates cache size. Shorten recording length if possible.")
    return {
        "total_bytes": total_bytes,
        "total_human": _human_bytes(total_bytes),
        "components": components,
        "recommendations": recommendations,
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
    markdown_path: str | None = None
    websocket_path: str | None = None

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
                    if entry.get("markdown_path"):
                        markdown_path = entry["markdown_path"]
                    if entry.get("websocket_path"):
                        websocket_path = entry["websocket_path"]
    except OSError:
        pass

    # Trace file convention: <log>.trace.zip (set by `BrowserSession.close()`).
    candidate_trace = jsonl_path.with_suffix(".trace.zip")
    if trace_path is None and candidate_trace.exists():
        trace_path = str(candidate_trace)

    candidate_markdown = jsonl_path.with_suffix(".markdown.md")
    if markdown_path is None and candidate_markdown.exists():
        markdown_path = str(candidate_markdown)
    candidate_websocket = jsonl_path.with_suffix(".websocket.jsonl")
    if websocket_path is None and candidate_websocket.exists():
        websocket_path = str(candidate_websocket)
    return {
        **counts,
        "title": title,
        "video_path": video_path,
        "trace_path": trace_path,
        "markdown_path": markdown_path,
        "websocket_path": websocket_path,
        "url": last_url,
    }


def _cache_report_for_recording(jsonl_path: Path) -> dict[str, Any]:
    """Build a simple cache summary for a recording's artifacts."""
    instance_id = _instance_id_from_recording_name(jsonl_path.stem)
    artefacts = _scan_recording_artefacts(jsonl_path)
    return _build_cache_components(
        session_id=instance_id,
        jsonl_path=jsonl_path,
        markdown_path=Path(artefacts["markdown_path"]) if artefacts["markdown_path"] else None,
        trace_path=Path(artefacts["trace_path"]) if artefacts["trace_path"] else None,
        video_path=Path(artefacts["video_path"]) if artefacts["video_path"] else None,
        websocket_path=Path(artefacts["websocket_path"]) if artefacts["websocket_path"] else None,
    )


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
    return pool.maybe_get(session_id)


def _resolve_log_path(session_id: str) -> Path | None:
    live = _live_session_or_none(session_id)
    if live is not None:
        return Path(live.log_path)
    return _find_recording_for(session_id, state.RECORDINGS_DIR)


def _resolve_video_path(session_id: str) -> Path | None:
    live = _live_session_or_none(session_id)
    if live is not None and live.video_path is not None:
        return Path(live.video_path)
    jsonl = _find_recording_for(session_id, state.RECORDINGS_DIR)
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
    jsonl = _find_recording_for(session_id, state.RECORDINGS_DIR)
    if jsonl is None:
        return None
    artefacts = _scan_recording_artefacts(jsonl)
    if artefacts["trace_path"]:
        return Path(artefacts["trace_path"])
    return None


def _resolve_markdown_path(session_id: str) -> Path | None:
    live = _live_session_or_none(session_id)
    if live is not None and live.markdown_path is not None:
        return Path(live.markdown_path)
    jsonl = _find_recording_for(session_id, state.RECORDINGS_DIR)
    if jsonl is None:
        return None
    artefacts = _scan_recording_artefacts(jsonl)
    if artefacts["markdown_path"]:
        return Path(artefacts["markdown_path"])
    return None


# ---------------------------------------------------------------------------
# JSONL tail (matches `browser_tail_recording` semantics so the WS payloads
# look identical to the existing MCP tool — the frontend can speak one shape).
# ---------------------------------------------------------------------------


def _tail_jsonl(log_path: Path, since: int) -> dict[str, Any]:
    if not log_path.exists():
        return {"events": [], "cursor": since, "total_bytes": 0, "complete": True}

    def _looks_like_binary_preview(value: object) -> bool:
        return isinstance(value, str) and (
            (value.startswith('b"') and value.endswith('"')) or (value.startswith("b'") and value.endswith("'"))
        )

    def _sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
        action = event.get("action", "")
        if not (isinstance(action, str) and action.startswith("websocket_")):
            return event
        preview = event.get("payload_preview")
        is_binary = event.get("is_binary") is True or _looks_like_binary_preview(preview)
        if not (isinstance(preview, str) and (is_binary or _looks_like_binary_preview(preview))):
            return event
        size = event.get("payload_size")
        event["payload_preview"] = (
            f"[binary payload hidden: {size} bytes]" if isinstance(size, int) else "[binary payload hidden]"
        )
        if event.get("is_binary") is not True:
            event["is_binary"] = True
        return event

    events, new_cursor, total_bytes = tail_log(log_path, since)
    return {
        "events": [_sanitize_event(event) for event in events],
        "cursor": new_cursor,
        "total_bytes": total_bytes,
        "complete": new_cursor == total_bytes,
    }
