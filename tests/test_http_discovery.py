# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for discovery-layer caches: per-file summary cache and the recording-id
index's negative-cache via dir mtime."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.http import discovery


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    discovery.invalidate_recording_index()
    discovery._summary_per_file.clear()


def _write_recording(rec_dir: Path, instance_id: str, *, kind: str = "chromium") -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    path = rec_dir / f"20260101T000000Z-{kind}-{instance_id}.jsonl"
    path.write_text(
        json.dumps({"action": "launch", "kind": kind, "ts": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    return path


def test_summarise_recording_caches_per_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First call reads + parses; second call hits cache, no re-parse."""
    rec = tmp_path / "recordings"
    jsonl = _write_recording(rec, "abc12345dead")

    parse_count = 0
    real = discovery._summarise_recording

    def counting(jsonl_path: Path):  # type: ignore[no-untyped-def]
        nonlocal parse_count
        parse_count += 1
        return real(jsonl_path)

    monkeypatch.setattr(discovery, "_summarise_recording", counting)

    s1 = discovery._summarise_recording_cached(jsonl)
    s2 = discovery._summarise_recording_cached(jsonl)
    assert s1 == s2
    assert parse_count == 1, "second call should hit cache, not re-parse"


def test_summarise_recording_invalidates_when_signature_changes(tmp_path: Path) -> None:
    """A file with the same path but a fresh signature gets re-parsed."""
    rec = tmp_path / "recordings"
    jsonl = _write_recording(rec, "rebuiltidwxyz")

    s1 = discovery._summarise_recording_cached(jsonl)
    assert s1 is not None
    assert s1["kind"] == "chromium"

    # Rewrite with a different launch kind; signature (mtime/size) changes.
    jsonl.write_text(
        json.dumps({"action": "launch", "kind": "firefox", "ts": "2026-01-02T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    # Bump mtime explicitly in case the rewrite landed in the same nanosecond.
    import os

    new_mtime = jsonl.stat().st_mtime_ns + 1_000_000_000
    os.utime(jsonl, ns=(new_mtime, new_mtime))

    s2 = discovery._summarise_recording_cached(jsonl)
    assert s2 is not None
    assert s2["kind"] == "firefox"


def test_invalidate_recording_summary_drops_entry(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    jsonl = _write_recording(rec, "evictidqrstu")
    discovery._summarise_recording_cached(jsonl)
    assert str(jsonl) in discovery._summary_per_file

    discovery.invalidate_recording_summary(jsonl)
    assert str(jsonl) not in discovery._summary_per_file


def test_closed_sessions_uses_per_file_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated _closed_sessions calls don't re-parse JSONL files when nothing changed."""
    rec = tmp_path / "recordings"
    for i in range(5):
        _write_recording(rec, f"sess{i:08x}xxxx")

    parse_count = 0
    real = discovery._summarise_recording

    def counting(jsonl_path: Path):  # type: ignore[no-untyped-def]
        nonlocal parse_count
        parse_count += 1
        return real(jsonl_path)

    monkeypatch.setattr(discovery, "_summarise_recording", counting)

    discovery._closed_sessions(rec, set())  # cold: 5 parses
    assert parse_count == 5

    discovery._closed_sessions(rec, set())  # warm: 0 parses
    assert parse_count == 5

    discovery._closed_sessions(rec, set())  # warm: still 0 parses
    assert parse_count == 5


def test_find_recording_for_skips_rebuild_on_unchanged_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated bad-id lookups must not re-walk the dir when nothing has changed."""
    rec = tmp_path / "recordings"
    _write_recording(rec, "realsessionid")

    build_count = 0
    real = discovery._build_recording_index

    def counting(d: Path) -> dict[str, Path]:
        nonlocal build_count
        build_count += 1
        return real(d)

    monkeypatch.setattr(discovery, "_build_recording_index", counting)

    # First lookup builds the index once.
    assert discovery._find_recording_for("realsessionid", rec) is not None
    assert build_count == 1

    # Repeated bad-id lookups should NOT re-build (the prior bug).
    for _ in range(5):
        assert discovery._find_recording_for("nope" + ("x" * 8), rec) is None
    assert build_count == 1, f"expected 1 build, got {build_count} (negative-cache regression)"


def test_find_recording_for_rebuilds_when_dir_mtime_changes(tmp_path: Path) -> None:
    """When a new recording is added, the dir mtime changes and the next
    lookup picks it up."""
    rec = tmp_path / "recordings"
    _write_recording(rec, "originalidxxxx")

    # Prime the index.
    assert discovery._find_recording_for("originalidxxxx", rec) is not None
    assert discovery._find_recording_for("notyetidwxyz", rec) is None  # builds + caches negative

    # Add a new recording — dir mtime changes.
    new_jsonl = _write_recording(rec, "notyetidwxyz")
    # macOS Python sometimes reports mtime at second resolution; force a tick.
    import os

    new_mtime = rec.stat().st_mtime_ns + 1_000_000_000
    os.utime(rec, ns=(new_mtime, new_mtime))

    # Now the previously-unknown id should be findable.
    assert discovery._find_recording_for("notyetidwxyz", rec) == new_jsonl
