# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from octowright.defaults import DOWNLOAD_PATH_EXISTS_TTL_SECONDS, SESSION_ARTIFACT_CACHE_MAX_ENTRIES
from octowright.http.artifacts import cache_report_for_recording, scan_recording_artifacts


def iter_jsonl_entries(jsonl_path: Path) -> Iterator[dict[str, Any]]:
    """Yield each parsed JSON object from a JSONL file.

    Skips blank lines and lines that don't decode as JSON or aren't dicts.
    Returns an empty iterator if the file is missing or unreadable.
    Shared by the cache's sidecar-write path and the route fallbacks so the
    file-walking + decode + skip-malformed loop only lives in one place.
    """
    if not jsonl_path.exists():
        return
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
                if isinstance(entry, dict):
                    yield entry
    except OSError:
        return


# Bumped whenever the sidecar payload shape changes incompatibly. Sidecars
# written with a different version are rejected on read so an upgraded
# daemon never serves rows from a stale-format file.
_SIDECAR_FORMAT_VERSION = 1


class SessionArtifactCache:
    """Shared cache for recording-derived artifacts used by HTTP routes.

    Each cache is an LRU bounded by ``max_entries`` keyed by jsonl path,
    invalidated by (mtime_ns, size) signature so any change to the
    underlying file refreshes the entry.

    Defaults are pulled from ``octowright.defaults`` at construction time
    (not import time), so tests can override the environment and instantiate
    a fresh cache without reloading the module.
    """

    def __init__(
        self,
        *,
        max_entries: int | None = None,
        path_exists_ttl_seconds: float | None = None,
    ) -> None:
        self._max_entries = max_entries if max_entries is not None else SESSION_ARTIFACT_CACHE_MAX_ENTRIES
        self._path_exists_ttl_seconds = (
            path_exists_ttl_seconds if path_exists_ttl_seconds is not None else DOWNLOAD_PATH_EXISTS_TTL_SECONDS
        )
        self._artifact_cache: OrderedDict[str, tuple[tuple[int, int], dict[str, Any]]] = OrderedDict()
        self._report_cache: OrderedDict[str, tuple[tuple[int, int], dict[str, Any]]] = OrderedDict()
        self._console_index_cache: OrderedDict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = OrderedDict()
        self._downloads_index_cache: OrderedDict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = OrderedDict()
        self._path_exists_cache: OrderedDict[str, tuple[float, bool]] = OrderedDict()

    def _signature(self, jsonl_path: Path) -> tuple[int, int] | None:
        try:
            stat = jsonl_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _lru_set(self, cache: OrderedDict[str, Any], key: str, value: Any) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._max_entries:
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

    @staticmethod
    def console_row_from_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
        if entry.get("action") != "console":
            return None
        row: dict[str, Any] = {"level": entry.get("level"), "text": entry.get("text", "")}
        if "page_index" in entry:
            row["page_index"] = entry.get("page_index")
        return row

    @staticmethod
    def download_row_from_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
        if entry.get("action") != "download_saved":
            return None
        return {
            "url": entry.get("url"),
            "suggested_filename": entry.get("suggested_filename"),
            "path": entry.get("path"),
            "timestamp": entry.get("timestamp"),
        }

    def _parse_source_signature(self, raw: object) -> tuple[int, int] | None:
        if not isinstance(raw, dict):
            return None
        # `cast` is required for ty (which narrows to dict[Unknown, Unknown]
        # after the isinstance guard). Mypy accepts the bare .get() call.
        raw_dict = cast(dict[str, object], raw)
        mtime_ns = raw_dict.get("mtime_ns")
        size = raw_dict.get("size")
        if not isinstance(mtime_ns, int) or not isinstance(size, int):
            return None
        return (mtime_ns, size)

    def _read_index_file(self, path: Path, expected_source_signature: tuple[int, int]) -> list[dict[str, Any]] | None:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(loaded, dict):
            return None
        if loaded.get("version") != _SIDECAR_FORMAT_VERSION:
            return None
        sidecar_source_signature = self._parse_source_signature(loaded.get("source"))
        if sidecar_source_signature != expected_source_signature:
            return None
        rows = loaded.get("rows")
        if not isinstance(rows, list):
            return None
        return [row for row in rows if isinstance(row, dict)]

    def _write_index_file(self, path: Path, rows: list[dict[str, Any]], source_signature: tuple[int, int]) -> None:
        payload = {
            "version": _SIDECAR_FORMAT_VERSION,
            "source": {"mtime_ns": source_signature[0], "size": source_signature[1]},
            "rows": rows,
        }
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
        rows = self._read_index_file(self._console_index_path(jsonl_path), signature)
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
        rows = self._read_index_file(self._downloads_index_path(jsonl_path), signature)
        if rows is None:
            return None
        self._lru_set(self._downloads_index_cache, key, (signature, rows))
        return rows

    def get_console_rows(self, jsonl_path: Path) -> list[dict[str, Any]]:
        """Return console rows for a recording, hitting in-memory cache → sidecar
        → fresh JSONL scan in that order. The fallback scan populates the
        in-memory cache so subsequent reads of the same recording (signature
        unchanged) skip the scan. Read-only — never writes a sidecar file."""
        from_index = self.read_console_index(jsonl_path)
        if from_index is not None:
            return from_index
        signature = self._signature(jsonl_path)
        rows = [
            row for entry in iter_jsonl_entries(jsonl_path) if (row := self.console_row_from_entry(entry)) is not None
        ]
        if signature is not None:
            self._lru_set(self._console_index_cache, str(jsonl_path), (signature, rows))
        return rows

    def get_download_rows(self, jsonl_path: Path) -> list[dict[str, Any]]:
        """Return download rows for a recording, hitting in-memory cache → sidecar
        → fresh JSONL scan in that order. The fallback scan populates the
        in-memory cache so subsequent reads of the same recording (signature
        unchanged) skip the scan. Read-only — never writes a sidecar file."""
        from_index = self.read_downloads_index(jsonl_path)
        if from_index is not None:
            return from_index
        signature = self._signature(jsonl_path)
        rows = [
            row for entry in iter_jsonl_entries(jsonl_path) if (row := self.download_row_from_entry(entry)) is not None
        ]
        if signature is not None:
            self._lru_set(self._downloads_index_cache, str(jsonl_path), (signature, rows))
        return rows

    def write_event_indexes(self, jsonl_path: Path) -> None:
        if not jsonl_path.exists():
            return
        console_rows: list[dict[str, Any]] = []
        download_rows: list[dict[str, Any]] = []
        for entry in iter_jsonl_entries(jsonl_path):
            row = self.console_row_from_entry(entry)
            if row is not None:
                console_rows.append(row)
                continue
            download_row = self.download_row_from_entry(entry)
            if download_row is not None:
                download_rows.append(download_row)

        signature = self._signature(jsonl_path)
        if signature is None:
            return
        self._write_index_file(self._console_index_path(jsonl_path), console_rows, signature)
        self._write_index_file(self._downloads_index_path(jsonl_path), download_rows, signature)
        key = str(jsonl_path)
        self._lru_set(self._console_index_cache, key, (signature, console_rows))
        self._lru_set(self._downloads_index_cache, key, (signature, download_rows))

    def path_exists(self, path: str) -> bool:
        now = time.monotonic()
        cached = self._path_exists_cache.get(path)
        if cached is not None and (now - cached[0]) <= self._path_exists_ttl_seconds:
            self._path_exists_cache.move_to_end(path)
            return cached[1]
        exists = Path(path).exists()
        self._lru_set(self._path_exists_cache, path, (now, exists))
        return exists


session_artifact_cache = SessionArtifactCache()
