# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

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


def test_path_exists_caches_result(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text("x", encoding="utf-8")
    missing = tmp_path / "missing"

    cache = SessionArtifactCache()
    assert cache.path_exists(str(real)) is True
    assert cache.path_exists(str(missing)) is False

    # Subsequent calls return the cached answer even if the filesystem changes.
    real.unlink()
    missing.write_text("now exists", encoding="utf-8")
    assert cache.path_exists(str(real)) is True
    assert cache.path_exists(str(missing)) is False
