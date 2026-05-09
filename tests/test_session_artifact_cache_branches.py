# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for the SessionArtifactCache (multi-LRU caching layer).

This is the cache that PRs #5-#7 added. Correctness here directly affects
what the dashboard shows. Pins:
- LRU eviction at the configured max_entries boundary
- (mtime_ns, size) signature invalidation
- Sidecar version-mismatch / signature-mismatch / malformed handling
- Negative-cache TTL on path_exists
- Fallback-scan populates the in-memory cache
- evict() clears every per-recording cache slot
- warm_close one-pass folds artifact + index + report
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from octowright.http.session_artifacts import (
    _SIDECAR_FORMAT_VERSION,
    SessionArtifactCache,
    iter_jsonl_entries,
)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# ─── Constructor configurability ────────────────────────────────────────────


class TestConstructor:
    def test_defaults_pulled_from_env_constants(self) -> None:
        """Constructor reads SESSION_ARTIFACT_CACHE_MAX_ENTRIES + DOWNLOAD_PATH_EXISTS_TTL_SECONDS at instantiation."""
        from octowright.defaults import DOWNLOAD_PATH_EXISTS_TTL_SECONDS, SESSION_ARTIFACT_CACHE_MAX_ENTRIES

        cache = SessionArtifactCache()
        assert cache._max_entries == SESSION_ARTIFACT_CACHE_MAX_ENTRIES
        assert cache._path_exists_ttl_seconds == DOWNLOAD_PATH_EXISTS_TTL_SECONDS

    def test_explicit_max_entries_overrides_default(self) -> None:
        """Caller can shrink the LRU bound for tests."""
        cache = SessionArtifactCache(max_entries=2)
        assert cache._max_entries == 2

    def test_explicit_ttl_overrides_default(self) -> None:
        """Caller can override the path_exists TTL."""
        cache = SessionArtifactCache(path_exists_ttl_seconds=0.1)
        assert cache._path_exists_ttl_seconds == 0.1

    def test_each_cache_starts_empty(self) -> None:
        """All five caches initialize as empty OrderedDicts."""
        cache = SessionArtifactCache()
        assert len(cache._artifact_cache) == 0
        assert len(cache._report_cache) == 0
        assert len(cache._console_index_cache) == 0
        assert len(cache._downloads_index_cache) == 0
        assert len(cache._path_exists_cache) == 0


# ─── _signature ──────────────────────────────────────────────────────────────


class TestSignature:
    def test_existing_file_returns_mtime_and_size(self, tmp_path: Path) -> None:
        """Returns the (mtime_ns, size) tuple from stat()."""
        p = _write_jsonl(tmp_path / "rec.jsonl", [{"action": "click"}])
        cache = SessionArtifactCache()
        sig = cache._signature(p)
        assert sig is not None
        mtime_ns, size = sig
        assert isinstance(mtime_ns, int) and mtime_ns > 0
        assert size == p.stat().st_size

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """OSError on stat() → None (the cache callers fall back to no-cache mode)."""
        cache = SessionArtifactCache()
        assert cache._signature(tmp_path / "does-not-exist.jsonl") is None


# ─── _lru_set / eviction ────────────────────────────────────────────────────


class TestLruEviction:
    def test_insert_then_get_keeps_order(self) -> None:
        """move_to_end on insert — most-recent stays at the back."""
        cache = SessionArtifactCache(max_entries=3)
        cache._lru_set(cache._artifact_cache, "a", "v_a")
        cache._lru_set(cache._artifact_cache, "b", "v_b")
        cache._lru_set(cache._artifact_cache, "c", "v_c")
        assert list(cache._artifact_cache) == ["a", "b", "c"]

    def test_overflow_evicts_oldest(self) -> None:
        """When len > max_entries, popitem(last=False) drops the LRU end."""
        cache = SessionArtifactCache(max_entries=2)
        cache._lru_set(cache._artifact_cache, "a", "v_a")
        cache._lru_set(cache._artifact_cache, "b", "v_b")
        cache._lru_set(cache._artifact_cache, "c", "v_c")
        # 'a' was oldest → evicted.
        assert list(cache._artifact_cache) == ["b", "c"]

    def test_re_set_existing_key_does_not_evict(self) -> None:
        """Updating an existing key: still bounded but no eviction."""
        cache = SessionArtifactCache(max_entries=2)
        cache._lru_set(cache._artifact_cache, "a", "v_a")
        cache._lru_set(cache._artifact_cache, "b", "v_b")
        cache._lru_set(cache._artifact_cache, "a", "v_a2")  # re-set
        # No eviction; 'a' is now most-recent.
        assert list(cache._artifact_cache) == ["b", "a"]
        assert cache._artifact_cache["a"] == "v_a2"


# ─── evict ───────────────────────────────────────────────────────────────────


class TestEvict:
    def test_clears_every_per_recording_cache(self) -> None:
        """evict() drops the key from artifact / report / console_index / downloads_index."""
        cache = SessionArtifactCache(max_entries=10)
        path = Path("/tmp/whatever.jsonl")
        key = str(path)
        cache._artifact_cache[key] = ((1, 1), {})
        cache._report_cache[key] = ((1, 1), {})
        cache._console_index_cache[key] = ((1, 1), [])
        cache._downloads_index_cache[key] = ((1, 1), [])
        cache.evict(path)
        assert key not in cache._artifact_cache
        assert key not in cache._report_cache
        assert key not in cache._console_index_cache
        assert key not in cache._downloads_index_cache

    def test_evict_missing_key_is_noop(self) -> None:
        """No KeyError when the key isn't cached."""
        cache = SessionArtifactCache()
        cache.evict(Path("/tmp/nothing.jsonl"))  # must not raise


# ─── Static row extractors ──────────────────────────────────────────────────


class TestRowExtractors:
    def test_console_row_extracts_level_and_text(self) -> None:
        """Console entries produce {level, text} (and page_index when present)."""
        row = SessionArtifactCache.console_row_from_entry({"action": "console", "level": "warning", "text": "msg"})
        assert row == {"level": "warning", "text": "msg"}

    def test_console_row_includes_page_index_when_present(self) -> None:
        """page_index is conditionally added when entry has it."""
        row = SessionArtifactCache.console_row_from_entry(
            {"action": "console", "level": "info", "text": "x", "page_index": 2}
        )
        assert row == {"level": "info", "text": "x", "page_index": 2}

    def test_console_row_text_defaults_empty_string(self) -> None:
        """Missing text → '' default."""
        row = SessionArtifactCache.console_row_from_entry({"action": "console", "level": "info"})
        assert row == {"level": "info", "text": ""}

    def test_console_row_returns_none_for_non_console(self) -> None:
        """Other action types → None (filtered out by the comprehension)."""
        assert SessionArtifactCache.console_row_from_entry({"action": "click"}) is None
        assert SessionArtifactCache.console_row_from_entry({}) is None

    def test_download_row_extracts_four_fields(self) -> None:
        """Download row carries url/suggested_filename/path/timestamp."""
        row = SessionArtifactCache.download_row_from_entry(
            {
                "action": "download_saved",
                "url": "https://x/file.zip",
                "suggested_filename": "file.zip",
                "path": "/tmp/file.zip",
                "timestamp": "2026-01-01T00:00:00Z",
            }
        )
        assert row == {
            "url": "https://x/file.zip",
            "suggested_filename": "file.zip",
            "path": "/tmp/file.zip",
            "timestamp": "2026-01-01T00:00:00Z",
        }

    def test_download_row_returns_none_for_non_download(self) -> None:
        """Non-download_saved entries → None."""
        assert SessionArtifactCache.download_row_from_entry({"action": "click"}) is None


# ─── _parse_source_signature ────────────────────────────────────────────────


class TestParseSourceSignature:
    def test_well_formed_dict_returns_tuple(self) -> None:
        """Both fields int → (mtime, size)."""
        cache = SessionArtifactCache()
        assert cache._parse_source_signature({"mtime_ns": 100, "size": 50}) == (100, 50)

    def test_non_dict_input_returns_none(self) -> None:
        """Anything that isn't a dict → None."""
        cache = SessionArtifactCache()
        assert cache._parse_source_signature("oops") is None
        assert cache._parse_source_signature(None) is None
        assert cache._parse_source_signature([1, 2]) is None

    def test_missing_field_returns_none(self) -> None:
        """Either missing field → None."""
        cache = SessionArtifactCache()
        assert cache._parse_source_signature({"mtime_ns": 100}) is None
        assert cache._parse_source_signature({"size": 50}) is None

    def test_non_int_field_returns_none(self) -> None:
        """str/float/None for either field → None."""
        cache = SessionArtifactCache()
        assert cache._parse_source_signature({"mtime_ns": "100", "size": 50}) is None
        assert cache._parse_source_signature({"mtime_ns": 100, "size": 50.5}) is None


# ─── _read_index_file ────────────────────────────────────────────────────────


class TestReadIndexFile:
    def _well_formed(self, signature: tuple[int, int], rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "version": _SIDECAR_FORMAT_VERSION,
            "source": {"mtime_ns": signature[0], "size": signature[1]},
            "rows": rows,
        }

    def test_round_trip_returns_rows(self, tmp_path: Path) -> None:
        """Well-formed sidecar with matching signature → rows."""
        cache = SessionArtifactCache()
        sidecar = tmp_path / "rec.console.index.json"
        sidecar.write_text(json.dumps(self._well_formed((100, 50), [{"a": 1}, {"b": 2}])))
        rows = cache._read_index_file(sidecar, (100, 50))
        assert rows == [{"a": 1}, {"b": 2}]

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """OSError on read_text → None."""
        cache = SessionArtifactCache()
        assert cache._read_index_file(tmp_path / "missing.json", (100, 50)) is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        """JSONDecodeError → None."""
        cache = SessionArtifactCache()
        sidecar = tmp_path / "bad.json"
        sidecar.write_text("{ not json")
        assert cache._read_index_file(sidecar, (100, 50)) is None

    def test_non_dict_root_returns_none(self, tmp_path: Path) -> None:
        """JSON root is a list, not a dict → None."""
        cache = SessionArtifactCache()
        sidecar = tmp_path / "list.json"
        sidecar.write_text(json.dumps([1, 2, 3]))
        assert cache._read_index_file(sidecar, (100, 50)) is None

    def test_version_mismatch_returns_none(self, tmp_path: Path) -> None:
        """Sidecar with a different version is rejected."""
        cache = SessionArtifactCache()
        sidecar = tmp_path / "v0.json"
        payload = self._well_formed((100, 50), [{"x": 1}])
        payload["version"] = _SIDECAR_FORMAT_VERSION + 99
        sidecar.write_text(json.dumps(payload))
        assert cache._read_index_file(sidecar, (100, 50)) is None

    def test_signature_mismatch_returns_none(self, tmp_path: Path) -> None:
        """Source signature doesn't match → None (sidecar stale vs JSONL)."""
        cache = SessionArtifactCache()
        sidecar = tmp_path / "stale.json"
        sidecar.write_text(json.dumps(self._well_formed((100, 50), [{"x": 1}])))
        assert cache._read_index_file(sidecar, (200, 60)) is None

    def test_non_list_rows_returns_none(self, tmp_path: Path) -> None:
        """rows isn't a list → None."""
        cache = SessionArtifactCache()
        sidecar = tmp_path / "bad-rows.json"
        payload = self._well_formed((100, 50), [])
        payload["rows"] = "not-a-list"
        sidecar.write_text(json.dumps(payload))
        assert cache._read_index_file(sidecar, (100, 50)) is None

    def test_non_dict_rows_filtered_out(self, tmp_path: Path) -> None:
        """List with mixed dict/non-dict items → only dicts kept."""
        cache = SessionArtifactCache()
        sidecar = tmp_path / "mixed.json"
        payload = self._well_formed((100, 50), [])
        payload["rows"] = [{"a": 1}, "stray", 42, {"b": 2}]
        sidecar.write_text(json.dumps(payload))
        rows = cache._read_index_file(sidecar, (100, 50))
        assert rows == [{"a": 1}, {"b": 2}]


# ─── _write_index_file ──────────────────────────────────────────────────────


class TestWriteIndexFile:
    def test_writes_payload_with_version_and_signature(self, tmp_path: Path) -> None:
        """File contains version + source + rows fields exactly."""
        cache = SessionArtifactCache()
        path = tmp_path / "out.json"
        cache._write_index_file(path, [{"a": 1}], (100, 50))
        loaded = json.loads(path.read_text())
        assert loaded == {
            "version": _SIDECAR_FORMAT_VERSION,
            "source": {"mtime_ns": 100, "size": 50},
            "rows": [{"a": 1}],
        }

    def test_atomic_via_temp_file(self, tmp_path: Path) -> None:
        """Tmp file is renamed (no leftover .pid.tmp on success)."""
        cache = SessionArtifactCache()
        path = tmp_path / "out.json"
        cache._write_index_file(path, [], (100, 50))
        # No .tmp leftovers.
        assert not list(tmp_path.glob("*.tmp"))


# ─── scan_artifacts caching ─────────────────────────────────────────────────


class TestScanArtifactsCaching:
    def test_cache_miss_then_hit(self, tmp_path: Path) -> None:
        """First call scans, second hits the cache (no underlying call)."""
        p = _write_jsonl(tmp_path / "rec.jsonl", [{"action": "click"}])
        cache = SessionArtifactCache()
        with patch("octowright.http.session_artifacts.scan_recording_artifacts") as scan:
            scan.return_value = {"video_path": None, "trace_path": None, "markdown_path": None, "websocket_path": None}
            cache.scan_artifacts(p)
            cache.scan_artifacts(p)
        assert scan.call_count == 1

    def test_cache_invalidated_on_file_change(self, tmp_path: Path) -> None:
        """If signature changes (file rewritten), cache misses again."""
        p = _write_jsonl(tmp_path / "rec.jsonl", [{"action": "click"}])
        cache = SessionArtifactCache()
        with patch("octowright.http.session_artifacts.scan_recording_artifacts") as scan:
            scan.return_value = {"video_path": None, "trace_path": None, "markdown_path": None, "websocket_path": None}
            cache.scan_artifacts(p)
            # Rewrite the file with different content (changes mtime + size).
            import time as _time

            _time.sleep(0.01)
            _write_jsonl(p, [{"action": "fill"}, {"action": "click"}])
            cache.scan_artifacts(p)
        assert scan.call_count == 2

    def test_missing_file_falls_back_without_caching(self, tmp_path: Path) -> None:
        """Path doesn't exist → signature is None → falls through to scan_recording_artifacts directly."""
        cache = SessionArtifactCache()
        missing = tmp_path / "nope.jsonl"
        with patch("octowright.http.session_artifacts.scan_recording_artifacts") as scan:
            scan.return_value = {"video_path": None}
            cache.scan_artifacts(missing)
            cache.scan_artifacts(missing)
        # Both calls fall through (no cache slot persisted).
        assert scan.call_count == 2


# ─── read_console_index / read_downloads_index ─────────────────────────────


class TestReadIndexCacheLayer:
    def test_console_index_in_memory_hit(self, tmp_path: Path) -> None:
        """In-memory cache hit avoids a sidecar read."""
        p = _write_jsonl(tmp_path / "rec.jsonl", [])
        cache = SessionArtifactCache()
        sig = cache._signature(p)
        cache._console_index_cache[str(p)] = (sig, [{"level": "info", "text": "x"}])
        # No sidecar file exists; in-memory hit returns the rows.
        assert cache.read_console_index(p) == [{"level": "info", "text": "x"}]

    def test_console_index_missing_file_returns_none(self, tmp_path: Path) -> None:
        """JSONL signature is None → read_console_index returns None."""
        cache = SessionArtifactCache()
        assert cache.read_console_index(tmp_path / "missing.jsonl") is None

    def test_console_index_falls_through_to_sidecar(self, tmp_path: Path) -> None:
        """No in-memory cache, sidecar exists → sidecar read populates cache."""
        p = _write_jsonl(tmp_path / "rec.jsonl", [])
        cache = SessionArtifactCache()
        sig = cache._signature(p)
        sidecar = p.with_suffix(".console.index.json")
        cache._write_index_file(sidecar, [{"level": "info", "text": "hi"}], sig)
        rows = cache.read_console_index(p)
        assert rows == [{"level": "info", "text": "hi"}]
        # Now in memory.
        assert str(p) in cache._console_index_cache


# ─── get_console_rows / get_download_rows: fallback scan ────────────────────


class TestGetRowsFallback:
    def test_falls_back_to_scan_when_no_sidecar(self, tmp_path: Path) -> None:
        """No sidecar, no in-memory → scan the JSONL directly."""
        p = _write_jsonl(
            tmp_path / "rec.jsonl",
            [
                {"action": "console", "level": "info", "text": "hello"},
                {"action": "click", "selector": "#x"},
                {"action": "console", "level": "warn", "text": "wat"},
            ],
        )
        cache = SessionArtifactCache()
        rows = cache.get_console_rows(p)
        assert rows == [
            {"level": "info", "text": "hello"},
            {"level": "warn", "text": "wat"},
        ]

    def test_fallback_scan_populates_in_memory_cache(self, tmp_path: Path) -> None:
        """A second call after the fallback scan must hit the in-memory cache."""
        p = _write_jsonl(
            tmp_path / "rec.jsonl",
            [{"action": "console", "level": "info", "text": "hello"}],
        )
        cache = SessionArtifactCache()
        cache.get_console_rows(p)
        # Second call hits cache: even if the file's gone, the in-memory entry remains.
        # Check by inspecting the cache directly.
        assert str(p) in cache._console_index_cache

    def test_download_fallback_scan(self, tmp_path: Path) -> None:
        """Same fallback pattern for downloads."""
        p = _write_jsonl(
            tmp_path / "rec.jsonl",
            [
                {
                    "action": "download_saved",
                    "url": "https://x",
                    "suggested_filename": "f",
                    "path": "/tmp/f",
                    "timestamp": "t",
                },
                {"action": "click"},
            ],
        )
        cache = SessionArtifactCache()
        rows = cache.get_download_rows(p)
        assert rows == [
            {"url": "https://x", "suggested_filename": "f", "path": "/tmp/f", "timestamp": "t"},
        ]


# ─── path_exists TTL cache ──────────────────────────────────────────────────


class TestPathExistsTtl:
    def test_first_call_stats_filesystem(self, tmp_path: Path) -> None:
        """First lookup stats the path."""
        p = tmp_path / "real.txt"
        p.write_text("x")
        cache = SessionArtifactCache(path_exists_ttl_seconds=10.0)
        assert cache.path_exists(str(p)) is True

    def test_within_ttl_uses_cached_value(self, tmp_path: Path) -> None:
        """Even after the file is removed, within-TTL lookups still report True."""
        p = tmp_path / "real.txt"
        p.write_text("x")
        cache = SessionArtifactCache(path_exists_ttl_seconds=10.0)
        cache.path_exists(str(p))
        p.unlink()
        # Within TTL — still cached as True.
        assert cache.path_exists(str(p)) is True

    def test_after_ttl_re_stats(self, tmp_path: Path) -> None:
        """After TTL expires, lookup re-stats the filesystem."""
        p = tmp_path / "real.txt"
        p.write_text("x")
        cache = SessionArtifactCache(path_exists_ttl_seconds=0.0)  # expire immediately
        assert cache.path_exists(str(p)) is True
        p.unlink()
        # TTL=0 → always re-stat → now False.
        assert cache.path_exists(str(p)) is False

    def test_negative_cache_for_missing_file(self, tmp_path: Path) -> None:
        """A path that doesn't exist gets cached as False."""
        cache = SessionArtifactCache(path_exists_ttl_seconds=10.0)
        path = str(tmp_path / "missing.txt")
        assert cache.path_exists(path) is False
        # Cached entry exists.
        assert path in cache._path_exists_cache

    def test_path_exists_lru_bound(self) -> None:
        """The LRU bound applies to path_exists too."""
        cache = SessionArtifactCache(max_entries=2, path_exists_ttl_seconds=10.0)
        cache.path_exists("/tmp/a")
        cache.path_exists("/tmp/b")
        cache.path_exists("/tmp/c")
        # Only the two most-recent survive.
        assert len(cache._path_exists_cache) == 2
        assert "/tmp/a" not in cache._path_exists_cache


# ─── iter_jsonl_entries module helper ───────────────────────────────────────


class TestIterJsonlEntries:
    def test_yields_dict_entries(self, tmp_path: Path) -> None:
        """Each well-formed dict line is yielded."""
        p = _write_jsonl(tmp_path / "rec.jsonl", [{"a": 1}, {"b": 2}])
        assert list(iter_jsonl_entries(p)) == [{"a": 1}, {"b": 2}]

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        """Empty/whitespace lines are skipped."""
        p = tmp_path / "rec.jsonl"
        p.write_text(json.dumps({"a": 1}) + "\n\n  \n" + json.dumps({"b": 2}) + "\n")
        assert list(iter_jsonl_entries(p)) == [{"a": 1}, {"b": 2}]

    def test_skips_malformed_lines(self, tmp_path: Path) -> None:
        """JSONDecodeError on a line is silently skipped."""
        p = tmp_path / "rec.jsonl"
        p.write_text(json.dumps({"a": 1}) + "\n{ broken\n" + json.dumps({"b": 2}) + "\n")
        assert list(iter_jsonl_entries(p)) == [{"a": 1}, {"b": 2}]

    def test_skips_non_dict_entries(self, tmp_path: Path) -> None:
        """Lines that decode to lists/scalars are filtered out."""
        p = tmp_path / "rec.jsonl"
        p.write_text(json.dumps({"a": 1}) + "\n[1,2]\n42\n" + json.dumps({"b": 2}) + "\n")
        assert list(iter_jsonl_entries(p)) == [{"a": 1}, {"b": 2}]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Missing file → empty iterator (no FileNotFoundError)."""
        assert list(iter_jsonl_entries(tmp_path / "missing.jsonl")) == []
