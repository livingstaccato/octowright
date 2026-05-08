# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.http.session_artifacts import SessionArtifactCache, iter_jsonl_entries


def _append_jsonl(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_iter_jsonl_entries_skips_blank_decode_errors_and_non_dicts(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        fh.write('{"action": "console", "level": "log", "text": "a"}\n')
        fh.write("\n")  # blank
        fh.write("not-json\n")
        fh.write("[1, 2, 3]\n")  # JSON but not a dict
        fh.write('{"action": "download_saved", "url": "u"}\n')
    rows = list(iter_jsonl_entries(jsonl))
    assert [r["action"] for r in rows] == ["console", "download_saved"]


def test_iter_jsonl_entries_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert list(iter_jsonl_entries(tmp_path / "does_not_exist.jsonl")) == []


def test_read_index_returns_none_without_sidecar(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _append_jsonl(jsonl, {"action": "console", "level": "log", "text": "hi"})

    cache = SessionArtifactCache()
    assert cache.read_console_index(jsonl) is None
    assert cache.read_downloads_index(jsonl) is None


def test_write_event_indexes_filters_and_round_trips(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _append_jsonl(jsonl, {"action": "launch", "kind": "chromium"})  # filtered out
    _append_jsonl(jsonl, {"action": "console", "level": "log", "text": "a", "page_index": 0})
    _append_jsonl(jsonl, {"action": "console", "level": "warn", "text": "b"})
    _append_jsonl(
        jsonl,
        {
            "action": "download_saved",
            "url": "https://x.test/a",
            "suggested_filename": "a.txt",
            "path": "/tmp/a.txt",
            "timestamp": "t0",
        },
    )

    cache = SessionArtifactCache()
    cache.write_event_indexes(jsonl)

    assert jsonl.with_suffix(".console.index.json").exists()
    assert jsonl.with_suffix(".downloads.index.json").exists()

    console = cache.read_console_index(jsonl)
    downloads = cache.read_downloads_index(jsonl)
    assert console == [
        {"level": "log", "text": "a", "page_index": 0},
        {"level": "warn", "text": "b"},
    ]
    assert downloads == [
        {
            "url": "https://x.test/a",
            "suggested_filename": "a.txt",
            "path": "/tmp/a.txt",
            "timestamp": "t0",
        }
    ]


def test_read_index_invalidates_when_jsonl_changes(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _append_jsonl(jsonl, {"action": "console", "level": "log", "text": "first"})

    cache = SessionArtifactCache()
    cache.write_event_indexes(jsonl)
    assert cache.read_console_index(jsonl) == [{"level": "log", "text": "first"}]

    # Rewrite the index sidecar to simulate stale data the cache should not serve
    # once the JSONL signature changes.
    _append_jsonl(jsonl, {"action": "console", "level": "log", "text": "second"})
    cache.write_event_indexes(jsonl)
    assert cache.read_console_index(jsonl) == [
        {"level": "log", "text": "first"},
        {"level": "log", "text": "second"},
    ]


def test_read_index_handles_corrupt_sidecar(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _append_jsonl(jsonl, {"action": "console", "level": "log", "text": "x"})
    sidecar = jsonl.with_suffix(".console.index.json")
    sidecar.write_text("not-json", encoding="utf-8")

    cache = SessionArtifactCache()
    assert cache.read_console_index(jsonl) is None


def test_read_index_rejects_sidecar_with_unknown_version(tmp_path: Path) -> None:
    """Future-format sidecars are rejected so an old daemon can't serve them."""
    jsonl = tmp_path / "session.jsonl"
    _append_jsonl(jsonl, {"action": "console", "level": "log", "text": "x"})
    stat = jsonl.stat()
    sidecar = jsonl.with_suffix(".console.index.json")
    sidecar.write_text(
        json.dumps(
            {
                "version": 99,
                "source": {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size},
                "rows": [{"level": "log", "text": "future-format"}],
            }
        ),
        encoding="utf-8",
    )

    cache = SessionArtifactCache()
    assert cache.read_console_index(jsonl) is None


def test_read_index_rejects_sidecar_with_missing_source_signature(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _append_jsonl(jsonl, {"action": "console", "level": "log", "text": "x"})
    sidecar = jsonl.with_suffix(".console.index.json")
    sidecar.write_text(
        json.dumps({"version": 1, "rows": [{"level": "log", "text": "stale"}]}),
        encoding="utf-8",
    )

    cache = SessionArtifactCache()
    assert cache.read_console_index(jsonl) is None


def test_row_extractors_filter_and_normalize() -> None:
    entry_console = {"action": "console", "level": "warn", "text": "x", "page_index": 3}
    entry_download = {
        "action": "download_saved",
        "url": "https://x.test/a",
        "suggested_filename": "a.txt",
        "path": "/tmp/a.txt",
        "timestamp": "now",
    }
    assert SessionArtifactCache.console_row_from_entry(entry_console) == {
        "level": "warn",
        "text": "x",
        "page_index": 3,
    }
    assert SessionArtifactCache.download_row_from_entry(entry_download) == {
        "url": "https://x.test/a",
        "suggested_filename": "a.txt",
        "path": "/tmp/a.txt",
        "timestamp": "now",
    }
    assert SessionArtifactCache.console_row_from_entry({"action": "click"}) is None
    assert SessionArtifactCache.download_row_from_entry({"action": "navigate"}) is None


def test_path_exists_cache_expires_after_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.http.session_artifacts as sa_mod

    path = tmp_path / "artifact.bin"
    path.write_text("ok", encoding="utf-8")
    cache = SessionArtifactCache(path_exists_ttl_seconds=2.0)

    fake_time = {"t": 1000.0}

    def _now() -> float:
        return fake_time["t"]

    monkeypatch.setattr(sa_mod.time, "monotonic", _now)
    assert cache.path_exists(str(path)) is True
    path.unlink()
    # Within TTL we still serve cached existence.
    fake_time["t"] += 1.0
    assert cache.path_exists(str(path)) is True
    # After TTL expiry cache refreshes from filesystem.
    fake_time["t"] += 1.5
    assert cache.path_exists(str(path)) is False


def test_index_cache_evicts_oldest_when_bound_exceeded(tmp_path: Path) -> None:
    """LRU bound prevents unbounded memory growth across many sessions."""
    bound = 8
    cache = SessionArtifactCache(max_entries=bound)
    paths = []
    for i in range(bound + 5):
        jsonl = tmp_path / f"s{i}.jsonl"
        _append_jsonl(jsonl, {"action": "console", "level": "log", "text": f"msg-{i}"})
        cache.write_event_indexes(jsonl)
        paths.append(jsonl)

    assert len(cache._console_index_cache) == bound
    # The five oldest entries should have been evicted.
    for old in paths[:5]:
        assert str(old) not in cache._console_index_cache


def test_evict_drops_all_caches_for_path(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _append_jsonl(jsonl, {"action": "console", "level": "log", "text": "x"})
    cache = SessionArtifactCache()
    cache.write_event_indexes(jsonl)
    cache.scan_artifacts(jsonl)

    key = str(jsonl)
    assert key in cache._console_index_cache
    assert key in cache._artifact_cache

    cache.evict(jsonl)
    assert key not in cache._console_index_cache
    assert key not in cache._artifact_cache


def test_constructor_defaults_pull_from_module_defaults() -> None:
    """Cache picks up defaults from octowright.defaults at construction time."""
    from octowright.defaults import DOWNLOAD_PATH_EXISTS_TTL_SECONDS, SESSION_ARTIFACT_CACHE_MAX_ENTRIES

    cache = SessionArtifactCache()
    assert cache._max_entries == SESSION_ARTIFACT_CACHE_MAX_ENTRIES
    assert cache._path_exists_ttl_seconds == DOWNLOAD_PATH_EXISTS_TTL_SECONDS
