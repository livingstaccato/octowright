# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def instance_id_from_recording_name(stem: str) -> str | None:
    parts = stem.split("-")
    if len(parts) < 3:
        return None
    return parts[2]


def _human_bytes(size_bytes: int) -> str:
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


EVENT_ONLY_ACTIONS = {"console", "download_saved", "popup_opened"}


# JSONL action → counter name mapping for the per-event tally.
_ACTION_COUNTERS: dict[str, str] = {
    "console": "console_count",
    "download_saved": "download_count",
    "popup_opened": "page_count",
}

# Sidecar artefacts captured on the `close` event. Each maps the entry-key
# to the local-state field it populates.
_CLOSE_SIDECARS: tuple[str, ...] = ("video_path", "trace_path", "markdown_path", "websocket_path")

# Sidecar files we look for next to the JSONL when the close event didn't
# record a path. Maps state-field-name → suffix to probe.
_FILESYSTEM_FALLBACKS: dict[str, str] = {
    "trace_path": ".trace.zip",
    "markdown_path": ".markdown.md",
    "websocket_path": ".websocket.jsonl",
}


def _ingest_entry(entry: dict[str, Any], state: dict[str, Any], counts: dict[str, int]) -> None:
    """Apply one JSONL entry's effects to the running state + counts."""
    counts["event_count"] += 1
    action = entry.get("action")
    if action not in EVENT_ONLY_ACTIONS:
        counts["action_count"] += 1
    counter_name = _ACTION_COUNTERS.get(action or "")
    if counter_name:
        counts[counter_name] += 1
    if action == "navigate" and entry.get("url"):
        state["url"] = entry["url"]
    if action == "close":
        for field in _CLOSE_SIDECARS:
            if entry.get(field):
                state[field] = entry[field]


def _tally_jsonl(jsonl_path: Path, state: dict[str, Any], counts: dict[str, int]) -> None:
    """Walk one JSONL file and feed each entry to _ingest_entry."""
    try:
        fh = jsonl_path.open(encoding="utf-8")
    except OSError:
        return
    with fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            _ingest_entry(entry, state, counts)


def _empty_artifact_state() -> tuple[dict[str, int], dict[str, Any]]:
    counts = {"event_count": 0, "action_count": 0, "console_count": 0, "download_count": 0, "page_count": 1}
    state: dict[str, Any] = {
        "title": None,
        "url": None,
        "video_path": None,
        "trace_path": None,
        "markdown_path": None,
        "websocket_path": None,
    }
    return counts, state


def _apply_filesystem_fallbacks(jsonl_path: Path, state: dict[str, Any]) -> None:
    """Probe the canonical sidecar filenames for any artefact path the close
    event didn't record explicitly."""
    for field, suffix in _FILESYSTEM_FALLBACKS.items():
        if state[field] is None:
            candidate = jsonl_path.with_suffix(suffix)
            if candidate.exists():
                state[field] = str(candidate)


def scan_recording_artifacts(jsonl_path: Path) -> dict[str, Any]:
    counts, state = _empty_artifact_state()
    _tally_jsonl(jsonl_path, state, counts)
    _apply_filesystem_fallbacks(jsonl_path, state)
    return {**counts, **state}


def ingest_entry(entry: dict[str, Any], state: dict[str, Any], counts: dict[str, int]) -> None:
    """Public alias for the per-entry ingestion step.

    Exposed so `SessionArtifactCache.warm_close` can fold artifact aggregation
    into the same JSONL walk that builds the row sidecars, instead of having
    two threads parse the same file twice on session close.
    """
    _ingest_entry(entry, state, counts)


def empty_artifact_state() -> tuple[dict[str, int], dict[str, Any]]:
    """Public alias for the artifact-state initializer (see ``ingest_entry``)."""
    return _empty_artifact_state()


def apply_filesystem_fallbacks(jsonl_path: Path, state: dict[str, Any]) -> None:
    """Public alias for the filesystem fallback step (see ``ingest_entry``)."""
    _apply_filesystem_fallbacks(jsonl_path, state)


def cache_report_for_recording(jsonl_path: Path) -> dict[str, Any]:
    instance_id = instance_id_from_recording_name(jsonl_path.stem)
    artifacts = scan_recording_artifacts(jsonl_path)
    return _build_cache_components(
        session_id=instance_id,
        jsonl_path=jsonl_path,
        markdown_path=Path(artifacts["markdown_path"]) if artifacts["markdown_path"] else None,
        trace_path=Path(artifacts["trace_path"]) if artifacts["trace_path"] else None,
        video_path=Path(artifacts["video_path"]) if artifacts["video_path"] else None,
        websocket_path=Path(artifacts["websocket_path"]) if artifacts["websocket_path"] else None,
    )
