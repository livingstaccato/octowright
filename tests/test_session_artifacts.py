# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.http.session_artifacts import SessionArtifactCache


def _append_jsonl(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


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


def test_index_cache_evicts_oldest_when_bound_exceeded(tmp_path: Path) -> None:
    """LRU bound prevents unbounded memory growth across many sessions."""
    from octowright.http.session_artifacts import _MAX_ENTRIES

    cache = SessionArtifactCache()
    paths = []
    for i in range(_MAX_ENTRIES + 5):
        jsonl = tmp_path / f"s{i}.jsonl"
        _append_jsonl(jsonl, {"action": "console", "level": "log", "text": f"msg-{i}"})
        cache.write_event_indexes(jsonl)
        paths.append(jsonl)

    assert len(cache._console_index_cache) == _MAX_ENTRIES
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


def test_max_entries_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCTOWRIGHT_SESSION_ARTIFACT_CACHE_MAX_ENTRIES propagates from env to module constant."""
    import importlib

    monkeypatch.setenv("OCTOWRIGHT_SESSION_ARTIFACT_CACHE_MAX_ENTRIES", "8")
    import octowright.defaults as defaults_mod

    importlib.reload(defaults_mod)
    import octowright.http.session_artifacts as sa_mod

    importlib.reload(sa_mod)
    assert sa_mod._MAX_ENTRIES == 8

    # Restore module state for subsequent tests in the same session.
    monkeypatch.delenv("OCTOWRIGHT_SESSION_ARTIFACT_CACHE_MAX_ENTRIES", raising=False)
    importlib.reload(defaults_mod)
    importlib.reload(sa_mod)
