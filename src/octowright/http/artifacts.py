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


def kind_from_recording_name(stem: str) -> str | None:
    """The session kind a recording's own filename carries.

    ``new_log_path`` builds ``<stamp>-<kind>-<id>[-<label>]``, and
    ``plugins.identity.KIND_RE`` forbids a hyphen in a kind by construction --
    the same invariant that lets the id above be recovered as ``parts[2]``. So
    the name is an authoritative source for the kind of every recording ever
    written, including one whose opening row is missing or unreadable.
    """
    parts = stem.split("-")
    if len(parts) < 3 or not parts[1]:
        return None
    return parts[1]


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


def _build_component(path: Path | None) -> dict[str, Any]:
    # One stat() call covers both existence and size — no separate exists()
    # probe. Called for 5 components per cache report, so the syscall halving
    # adds up on hot dashboard refreshes.
    if path is None:
        return {"size_bytes": 0, "size_human": _human_bytes(0), "path": None, "exists": False}
    try:
        size_bytes = path.stat().st_size
        exists = True
    except OSError:
        size_bytes = 0
        exists = False
    return {
        "size_bytes": size_bytes,
        "size_human": _human_bytes(size_bytes),
        "path": str(path),
        "exists": exists,
    }


# Per-recording-dir PNG listing cache. Keyed by dir mtime so a new
# screenshot, deletion, or rename invalidates automatically. Amortizes the
# readdir across every per-session lookup against the same dir — and per-
# session lookups happen on every /api/sessions/{id} for live sessions
# (no cache there) and on the first cache_report build for closed ones.
_screenshot_dir_cache: dict[Path, tuple[int, list[Path]]] = {}


def _list_screenshot_paths_cached(recording_dir: Path) -> list[Path]:
    try:
        dir_mtime = recording_dir.stat().st_mtime_ns
    except OSError:
        return []
    cached = _screenshot_dir_cache.get(recording_dir)
    if cached is not None and cached[0] == dir_mtime:
        return cached[1]
    paths = sorted(p for p in recording_dir.glob("*.png") if p.is_file())
    _screenshot_dir_cache[recording_dir] = (dir_mtime, paths)
    return paths


def invalidate_screenshot_dir_cache(recording_dir: Path | None = None) -> None:
    """Drop the cached PNG listing so the next call re-reads the dir."""
    if recording_dir is None:
        _screenshot_dir_cache.clear()
    else:
        _screenshot_dir_cache.pop(recording_dir, None)


def _find_screenshot_entries(recording_dir: Path, session_id: str | None) -> tuple[list[str], int]:
    """Sum + list every screenshot PNG produced for ``session_id`` in ``recording_dir``.

    The two known producers name files as:
      * ``{instance_id}-fail-{ts}.png`` — failure snapshots (``core_ops_mixin``).
      * ``{log_path.stem}.png`` — explicit captures via ``inspect`` tools, where
        ``log_path.stem`` always ends in ``-{instance_id}`` (see ``recorder.new_log_path``).

    Match by exact token boundary on those two patterns rather than the loose
    ``*{session_id}*.png`` glob, which would falsely match unrelated files
    that happen to contain the 12-char hex id as a substring.
    """
    if session_id is None:
        return [], 0
    total = 0
    entries: list[str] = []
    leading = f"{session_id}-"
    trailing = f"-{session_id}"
    for path in _list_screenshot_paths_cached(recording_dir):
        stem = path.stem
        if not (stem.startswith(leading) or stem.endswith(trailing)):
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


def ingest_entry(entry: dict[str, Any], state: dict[str, Any], counts: dict[str, int]) -> None:
    """Apply one JSONL entry's effects to the running state + counts.

    Used both by ``scan_recording_artifacts`` (one walk producing the artifact
    summary) and by ``SessionArtifactCache.warm_close`` (single-pass close
    aggregation that folds artifact + row extraction into one walk).
    """
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
    """Walk one JSONL file and feed each entry to ingest_entry."""
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
            ingest_entry(entry, state, counts)


def empty_artifact_state() -> tuple[dict[str, int], dict[str, Any]]:
    counts = {"event_count": 0, "action_count": 0, "console_count": 0, "download_count": 0, "page_count": 1}
    state: dict[str, Any] = {
        "url": None,
        "video_path": None,
        "trace_path": None,
        "markdown_path": None,
        "websocket_path": None,
    }
    return counts, state


def apply_filesystem_fallbacks(jsonl_path: Path, state: dict[str, Any]) -> None:
    """Probe the canonical sidecar filenames for any artefact path the close
    event didn't record explicitly."""
    for field, suffix in _FILESYSTEM_FALLBACKS.items():
        if state[field] is None:
            candidate = jsonl_path.with_suffix(suffix)
            if candidate.exists():
                state[field] = str(candidate)


def scan_recording_artifacts(jsonl_path: Path) -> dict[str, Any]:
    counts, state = empty_artifact_state()
    _tally_jsonl(jsonl_path, state, counts)
    apply_filesystem_fallbacks(jsonl_path, state)
    return {**counts, **state}


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
