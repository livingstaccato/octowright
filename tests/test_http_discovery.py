# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for discovery-layer caches: per-file summary cache and the recording-id
index's negative-cache via dir mtime."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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


def test_summarise_recording_classifies_closed_terminal(tmp_path: Path) -> None:
    """A closed terminal recording opens with terminal_start (no launch row);
    it must be classified kind='terminal', not 'unknown'."""
    rec = tmp_path / "recordings"
    rec.mkdir(parents=True, exist_ok=True)
    jsonl = rec / "20260101T000000Z-terminal-abc123def456.jsonl"
    jsonl.write_text(
        json.dumps({"action": "terminal_start", "connector_type": "pty", "ts": "2026-01-01T00:00:00Z"})
        + "\n"
        + json.dumps({"action": "terminal_output", "data": "hi"})
        + "\n"
        + json.dumps({"action": "terminal_stop", "reason": "eof"})
        + "\n",
        encoding="utf-8",
    )
    summary = discovery._summarise_recording(jsonl)
    assert summary is not None
    assert summary["kind"] == "terminal"
    assert summary["live"] is False
    assert summary["id"]  # instance id parsed from the filename
    # connector_type is in the terminal_start row and must survive to the summary
    # (the frontend renders pty/ssh/telnet differently).
    assert summary["connector_type"] == "pty"


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


def test_summary_cache_is_thread_safe_under_concurrent_load(tmp_path: Path) -> None:
    """Many threads hammering _summarise_recording_cached on distinct paths
    must not raise ``RuntimeError: dictionary changed size during iteration``
    (and every call must return a usable summary)."""
    rec = tmp_path / "recordings"
    # Use more paths than DISCOVERY_CACHE_MAX_ENTRIES so the LRU also exercises
    # popitem under contention; default is 256 so 320 distinct ids guarantees
    # eviction churn while the workers race.
    paths = [_write_recording(rec, f"thr{i:09x}") for i in range(320)]

    errors: list[BaseException] = []

    def worker(path: Path) -> None:
        try:
            for _ in range(10):
                summary = discovery._summarise_recording_cached(path)
                assert summary is not None
        except BaseException as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=50) as pool:
        # Submit every path; ThreadPoolExecutor schedules them across the
        # 50 workers so we get genuine cross-thread contention on the LRU.
        list(pool.map(worker, paths * 2))

    assert not errors, f"concurrent cache access raised: {errors[:3]}"


def test_recording_index_is_thread_safe_under_concurrent_load(tmp_path: Path) -> None:
    """Concurrent _find_recording_for callers must not race on the inner LRU."""
    rec = tmp_path / "recordings"
    ids = [f"idx{i:09x}" for i in range(80)]
    for sid in ids:
        _write_recording(rec, sid)

    errors: list[BaseException] = []

    def worker(sid: str) -> None:
        try:
            # Mix hits and misses to exercise both branches under contention.
            assert discovery._find_recording_for(sid, rec) is not None
            assert discovery._find_recording_for("missing__" + sid[:3], rec) is None
        except BaseException as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(worker, ids * 4))

    assert not errors, f"concurrent index access raised: {errors[:3]}"


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


def test_resolve_artifact_path_rejects_closed_session_path_outside_recordings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = tmp_path / "recordings"
    jsonl = _write_recording(rec, "artoutsidex1")
    outside = tmp_path / "outside.webm"
    outside.write_bytes(b"x")

    from octowright.http import state as http_state

    monkeypatch.setattr(http_state, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(discovery, "_live_session_or_none", lambda _sid: None)
    monkeypatch.setattr(discovery, "_find_recording_for", lambda _sid, _root: jsonl)
    monkeypatch.setattr(
        discovery.session_artifact_cache,
        "scan_artifacts",
        lambda _p: {"video_path": str(outside)},
    )

    assert discovery._resolve_artifact_path("artoutsidex1", "video_path") is None


def test_recordings_beyond_cache_cap_stay_addressable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With more recordings than the bounded index holds, a recording evicted
    from the index must STILL resolve — the dashboard lists it, so its detail
    endpoint must find it too. A saturated-index miss falls through to a
    targeted disk scan instead of the negative cache returning None."""
    monkeypatch.setattr(discovery, "DISCOVERY_CACHE_MAX_ENTRIES", 2)
    discovery.invalidate_recording_index()
    rec = tmp_path / "recordings"
    ids = ["aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"]
    for sid in ids:
        _write_recording(rec, sid)

    # Cap is 2 but there are 3 recordings — every one must resolve, including
    # the one evicted from the 2-entry index.
    for sid in ids:
        assert discovery._find_recording_for(sid, rec) is not None, sid

    # A genuinely-absent id still resolves to None (no false positives).
    assert discovery._find_recording_for("zzzzzzzzzzzz", rec) is None
