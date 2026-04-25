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

from ..server import _state
from . import state


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
