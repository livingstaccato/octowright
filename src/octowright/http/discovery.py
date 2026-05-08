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
from octowright.http.artifacts import instance_id_from_recording_name as _instance_id_from_recording_name
from octowright.http.session_artifacts import session_artifact_cache
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
    log_path = Path(session.log_path)
    # `started_at` is set on BrowserSession at construction (see core.py) so we
    # avoid a per-session JSONL open on every dashboard refresh. Fall back to
    # reading the first launch row only for sessions predating that field.
    started_at = getattr(session, "started_at", "") or None
    if not started_at:
        launch = _read_first_launch(log_path) if log_path.exists() else None
        started_at = (launch or {}).get("ts") or _iso(time.time())
    return {
        "id": session.instance_id,
        "kind": session.kind,
        "label": session.label,
        "profile": session.profile,
        "url": session.url,
        "started_at": started_at,
        "live": True,
        "log_path": str(log_path),
        "event_count": int(getattr(getattr(session, "recorder", None), "event_count", 0)),
        "console_count": int(getattr(session, "console_count", len(getattr(session, "console", ())))),
        "download_count": int(getattr(session, "download_count", len(getattr(session, "downloads", ())))),
        "page_count": int(getattr(session, "page_count", len(getattr(session, "pages", ()) or (1,)))),
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


# ---------------------------------------------------------------------------
# Session lookups (live OR on-disk recording)
# ---------------------------------------------------------------------------

# In-memory index of instance_id → JSONL path within a recordings dir, keyed
# by the dir to keep test isolation safe. Built lazily on first lookup; on a
# miss we rebuild rather than caching the negative answer, so a recording
# created out-of-band is picked up on its first request. ``invalidate_recording_index``
# is called from recording_cleanup so deletes don't leave stale entries.
_recording_index: dict[Path, dict[str, Path]] = {}


def _build_recording_index(recordings_dir: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    if not recordings_dir.exists():
        return index
    for jsonl in _iter_recordings(recordings_dir):
        sid = _instance_id_from_recording_name(jsonl.stem)
        if sid:
            index[sid] = jsonl
    return index


def invalidate_recording_index(recordings_dir: Path | None = None) -> None:
    """Drop the cached index so the next lookup rebuilds from disk.

    Pass a specific dir or ``None`` to clear all dirs (e.g. tests).
    """
    if recordings_dir is None:
        _recording_index.clear()
    else:
        _recording_index.pop(recordings_dir, None)


def _find_recording_for(session_id: str, recordings_dir: Path) -> Path | None:
    index = _recording_index.get(recordings_dir)
    if index is not None:
        hit = index.get(session_id)
        if hit is not None and hit.exists():
            return hit
        if hit is not None:
            # Cached path was deleted out-of-band; fall through to rebuild.
            del index[session_id]
    rebuilt = _build_recording_index(recordings_dir)
    _recording_index[recordings_dir] = rebuilt
    return rebuilt.get(session_id)


def _live_session_or_none(session_id: str) -> Any | None:
    pool = _state.pool
    return pool.maybe_get(session_id)


def _resolve_log_path(session_id: str) -> Path | None:
    live = _live_session_or_none(session_id)
    if live is not None:
        return Path(live.log_path)
    return _find_recording_for(session_id, state.RECORDINGS_DIR)


def _resolve_artifact_path(session_id: str, attr: str) -> Path | None:
    """Resolve a session-bound artifact path (live attr → recording → cached scan).

    ``attr`` names both the attribute on a live ``BrowserSession`` and the
    matching key in the artefact-scan dict (e.g. ``"video_path"``). Adding a
    new artifact type is a one-line caller addition rather than a copy of
    this whole pattern.
    """
    live = _live_session_or_none(session_id)
    if live is not None:
        live_path = getattr(live, attr, None)
        if live_path is not None:
            return Path(live_path)
    jsonl = _find_recording_for(session_id, state.RECORDINGS_DIR)
    if jsonl is None:
        return None
    artefacts = session_artifact_cache.scan_artifacts(jsonl)
    artefact = artefacts.get(attr)
    return Path(artefact) if artefact else None


def _resolve_video_path(session_id: str) -> Path | None:
    return _resolve_artifact_path(session_id, "video_path")


def _resolve_trace_path(session_id: str) -> Path | None:
    return _resolve_artifact_path(session_id, "trace_path")


def _resolve_markdown_path(session_id: str) -> Path | None:
    return _resolve_artifact_path(session_id, "markdown_path")


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
