# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from octowright.http.artifacts import cache_report_for_recording, scan_recording_artifacts

# Cap each per-cache to bound memory growth. Sized to comfortably exceed
# a reasonable working set of recently-viewed sessions while keeping
# total resident memory under a few MB.
_MAX_ENTRIES = 256


class SessionArtifactCache:
    """Shared cache for recording-derived artifacts used by HTTP routes.

    Each cache is an LRU bounded by ``_MAX_ENTRIES`` keyed by jsonl path,
    invalidated by (mtime_ns, size) signature so any change to the
    underlying file refreshes the entry.
    """

    def __init__(self) -> None:
        self._artifact_cache: OrderedDict[str, tuple[tuple[int, int], dict[str, Any]]] = OrderedDict()
        self._report_cache: OrderedDict[str, tuple[tuple[int, int], dict[str, Any]]] = OrderedDict()
        self._console_index_cache: OrderedDict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = OrderedDict()
        self._downloads_index_cache: OrderedDict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = OrderedDict()

    def _signature(self, jsonl_path: Path) -> tuple[int, int] | None:
        try:
            stat = jsonl_path.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return None

    @staticmethod
    def _lru_set(cache: OrderedDict[str, Any], key: str, value: Any) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > _MAX_ENTRIES:
            cache.popitem(last=False)

    def evict(self, jsonl_path: Path) -> None:
        """Drop any cached entries for ``jsonl_path`` (call on session deletion)."""
        key = str(jsonl_path)
        for cache in (
            self._artifact_cache,
            self._report_cache,
            self._console_index_cache,
            self._downloads_index_cache,
        ):
            cache.pop(key, None)

    def scan_artifacts(self, jsonl_path: Path) -> dict[str, Any]:
        signature = self._signature(jsonl_path)
        if signature is None:
            return scan_recording_artifacts(jsonl_path)
        key = str(jsonl_path)
        cached = self._artifact_cache.get(key)
        if cached and cached[0] == signature:
            self._artifact_cache.move_to_end(key)
            return cached[1]
        scanned = scan_recording_artifacts(jsonl_path)
        self._lru_set(self._artifact_cache, key, (signature, scanned))
        return scanned

    def cache_report(self, jsonl_path: Path) -> dict[str, Any]:
        signature = self._signature(jsonl_path)
        if signature is None:
            return cache_report_for_recording(jsonl_path)
        key = str(jsonl_path)
        cached = self._report_cache.get(key)
        if cached and cached[0] == signature:
            self._report_cache.move_to_end(key)
            return cached[1]
        report = cache_report_for_recording(jsonl_path)
        self._lru_set(self._report_cache, key, (signature, report))
        return report

    def _console_index_path(self, jsonl_path: Path) -> Path:
        return jsonl_path.with_suffix(".console.index.json")

    def _downloads_index_path(self, jsonl_path: Path) -> Path:
        return jsonl_path.with_suffix(".downloads.index.json")

    def _read_index_file(self, path: Path) -> list[dict[str, Any]] | None:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(loaded, dict):
            return None
        rows = loaded.get("rows")
        if not isinstance(rows, list):
            return None
        return [row for row in rows if isinstance(row, dict)]

    def _write_index_file(self, path: Path, rows: list[dict[str, Any]]) -> None:
        payload = {"version": 1, "rows": rows}
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)

    def read_console_index(self, jsonl_path: Path) -> list[dict[str, Any]] | None:
        signature = self._signature(jsonl_path)
        if signature is None:
            return None
        key = str(jsonl_path)
        cached = self._console_index_cache.get(key)
        if cached and cached[0] == signature:
            self._console_index_cache.move_to_end(key)
            return cached[1]
        rows = self._read_index_file(self._console_index_path(jsonl_path))
        if rows is None:
            return None
        self._lru_set(self._console_index_cache, key, (signature, rows))
        return rows

    def read_downloads_index(self, jsonl_path: Path) -> list[dict[str, Any]] | None:
        signature = self._signature(jsonl_path)
        if signature is None:
            return None
        key = str(jsonl_path)
        cached = self._downloads_index_cache.get(key)
        if cached and cached[0] == signature:
            self._downloads_index_cache.move_to_end(key)
            return cached[1]
        rows = self._read_index_file(self._downloads_index_path(jsonl_path))
        if rows is None:
            return None
        self._lru_set(self._downloads_index_cache, key, (signature, rows))
        return rows

    def write_event_indexes(self, jsonl_path: Path) -> None:
        if not jsonl_path.exists():
            return
        console_rows: list[dict[str, Any]] = []
        download_rows: list[dict[str, Any]] = []
        try:
            with jsonl_path.open(encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    action = entry.get("action")
                    if action == "console":
                        row = {"level": entry.get("level"), "text": entry.get("text", "")}
                        if "page_index" in entry:
                            row["page_index"] = entry.get("page_index")
                        console_rows.append(row)
                    elif action == "download_saved":
                        download_rows.append(
                            {
                                "url": entry.get("url"),
                                "suggested_filename": entry.get("suggested_filename"),
                                "path": entry.get("path"),
                                "timestamp": entry.get("timestamp"),
                            }
                        )
        except OSError:
            return

        self._write_index_file(self._console_index_path(jsonl_path), console_rows)
        self._write_index_file(self._downloads_index_path(jsonl_path), download_rows)
        signature = self._signature(jsonl_path)
        if signature is None:
            return
        key = str(jsonl_path)
        self._lru_set(self._console_index_cache, key, (signature, console_rows))
        self._lru_set(self._downloads_index_cache, key, (signature, download_rows))


session_artifact_cache = SessionArtifactCache()
