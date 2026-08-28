# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for discovery-layer caches: per-file summary cache and the recording-id
index's negative-cache via dir mtime."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from octowright.http import discovery


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    discovery.invalidate_recording_index()
    discovery.invalidate_closed_list()


def _write_recording(rec_dir: Path, instance_id: str, *, kind: str = "chromium") -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    path = rec_dir / f"20260101T000000Z-{kind}-{instance_id}.jsonl"
    path.write_text(
        json.dumps({"action": "launch", "kind": kind, "ts": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    return path


def test_listing_rereads_a_replaced_recording(tmp_path: Path) -> None:
    """A recording replaced at the same path is re-read once the directory changes.

    The listing snapshot keys on the directory mtime, and a summary is built
    from the opening row -- written once at launch and never rewritten, so an
    append cannot change it. A *different* recording at the same path only
    follows a delete, which bumps the directory mtime (and which
    recording_cleanup invalidates explicitly), so the per-file signature check
    on rebuild is what catches the swap.
    """
    rec = tmp_path / "recordings"
    jsonl = _write_recording(rec, "rebuiltidwxyz")
    assert discovery._summaries_for(rec)[0]["kind"] == "chromium"

    jsonl.write_text(
        json.dumps({"action": "launch", "kind": "firefox", "ts": "2026-01-02T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    stamp = jsonl.stat().st_mtime_ns + 1_000_000_000
    os.utime(jsonl, ns=(stamp, stamp))
    dir_stat = rec.stat()
    os.utime(rec, ns=(dir_stat.st_atime_ns, dir_stat.st_mtime_ns + 1_000_000_000))

    assert discovery._summaries_for(rec)[0]["kind"] == "firefox"


def test_summarise_recording_classifies_a_pre_wrapper_terminal_recording(tmp_path: Path) -> None:
    """An opening row with no ``kind`` still classifies, from the filename.

    Terminal recordings written before core's launch transaction existed open
    with the plugin's own ``terminal_start`` row, which carries no ``kind``.
    The name does: ``new_log_path`` builds ``<stamp>-<kind>-<id>``, and a kind
    may not contain the hyphen the name is split on, so it is exact rather than
    a guess -- and it answers for any opening row that lacks a kind, not just
    this one shape.
    """
    rec = tmp_path / "recordings"
    rec.mkdir(parents=True, exist_ok=True)
    jsonl = rec / "20260101T000000Z-terminal-abc123def456.jsonl"
    jsonl.write_text(
        json.dumps({"action": "terminal_start", "connector_type": "pty", "ts": "2026-01-01T00:00:00Z"})
        + "\n"
        + json.dumps({"action": "terminal_output", "data": "hi"})
        + "\n",
        encoding="utf-8",
    )
    summary = discovery._summarise_recording(jsonl)
    assert summary is not None
    assert summary["kind"] == "terminal"
    assert summary["live"] is False
    assert summary["id"] == "abc123def456"  # pragma: allowlist secret (fake instance id)


def test_an_opening_rows_kind_outranks_the_filename(tmp_path: Path) -> None:
    """The row core writes is authoritative; the name is only the fallback."""
    rec = tmp_path / "recordings"
    rec.mkdir(parents=True, exist_ok=True)
    jsonl = rec / "20260101T000000Z-terminal-abc123def456.jsonl"
    jsonl.write_text(
        json.dumps({"action": "session_start", "kind": "chromium", "ts": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    assert discovery._summarise_recording(jsonl)["kind"] == "chromium"


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


def test_listing_snapshot_is_thread_safe_under_concurrent_load(tmp_path: Path) -> None:
    """Many threads assembling the listing must not raise ``RuntimeError:
    dictionary changed size during iteration`` (and every call must return a
    usable listing). Discovery runs on the event loop AND on ``to_thread``
    workers, so the snapshot is written under the same lock the index uses."""
    rec = tmp_path / "recordings"
    for i in range(320):
        _write_recording(rec, f"thr{i:09x}")

    errors: list[BaseException] = []

    def worker(_: int) -> None:
        try:
            for _ in range(10):
                assert len(discovery._summaries_for(rec)) == 320
        except BaseException as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(worker, range(100)))

    assert not errors, f"concurrent listing access raised: {errors[:3]}"


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


def test_resolve_session_artifacts_returns_manifest_for_live_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A live session's manifest comes straight from its own attrs."""
    from types import SimpleNamespace

    log_path = tmp_path / "rec" / "x.jsonl"
    video_path = tmp_path / "rec" / "videos" / "x" / "x.webm"
    fake = SimpleNamespace(
        log_path=str(log_path),
        video_path=str(video_path),
        trace_path=None,
        har_path=None,
    )
    monkeypatch.setattr(discovery, "_live_session_or_none", lambda _sid: fake)
    monkeypatch.setattr(discovery, "safe_under", lambda _p, _root: True)

    manifest = discovery.resolve_session_artifacts("live1")

    assert manifest == {
        "log_path": str(log_path),
        "video_path": str(video_path),
        "trace_path": None,
        "har_path": None,
    }


def test_resolve_session_artifacts_finds_closed_session_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the feature: no live session, but a real recording on
    disk carries the close event's video/trace paths. This is the actual novel
    behavior browser_artifact_manifest exists for -- finding artifacts for a
    session the caller no longer holds a live handle to."""
    from octowright.http import state as http_state

    rec = tmp_path / "recordings"
    rec.mkdir()
    jsonl = rec / "20260101T000000Z-chromium-closedsess1.jsonl"
    video_path = rec / "videos" / "closedsess1" / "closedsess1.webm"
    trace_path = rec / "closedsess1.trace.zip"
    jsonl.write_text(
        "\n".join(
            [
                json.dumps({"action": "launch", "kind": "chromium", "ts": "2026-01-01T00:00:00Z"}),
                json.dumps({"action": "close", "video_path": str(video_path), "trace_path": str(trace_path)}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(http_state, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(discovery, "_live_session_or_none", lambda _sid: None)

    manifest = discovery.resolve_session_artifacts("closedsess1")

    assert manifest == {
        "log_path": str(jsonl),
        "video_path": str(video_path),
        "trace_path": str(trace_path),
        "har_path": None,
    }


def test_resolve_session_artifacts_returns_all_none_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """No live session and no matching recording on disk -> every field null."""
    monkeypatch.setattr(discovery, "_live_session_or_none", lambda _sid: None)
    monkeypatch.setattr(discovery, "_find_recording_for", lambda _sid, _root: None)

    manifest = discovery.resolve_session_artifacts("missingxxxxx")

    assert manifest == {
        "log_path": None,
        "video_path": None,
        "trace_path": None,
        "har_path": None,
    }


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


def test_saturated_index_caches_repeated_disk_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "DISCOVERY_CACHE_MAX_ENTRIES", 2)
    rec = tmp_path / "recordings"
    for sid in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
        _write_recording(rec, sid)

    scans = 0
    real_scan = discovery._scan_disk_for_recording

    def counting_scan(session_id: str, recordings_dir: Path) -> Path | None:
        nonlocal scans
        scans += 1
        return real_scan(session_id, recordings_dir)

    monkeypatch.setattr(discovery, "_scan_disk_for_recording", counting_scan)
    for _ in range(3):
        assert discovery._find_recording_for("aaaaaaaaaaaa", rec) is not None

    assert scans == 1


def test_saturated_index_caches_repeated_negative_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "DISCOVERY_CACHE_MAX_ENTRIES", 2)
    rec = tmp_path / "recordings"
    for sid in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
        _write_recording(rec, sid)

    scans = 0
    real_scan = discovery._scan_disk_for_recording

    def counting_scan(session_id: str, recordings_dir: Path) -> Path | None:
        nonlocal scans
        scans += 1
        return real_scan(session_id, recordings_dir)

    monkeypatch.setattr(discovery, "_scan_disk_for_recording", counting_scan)
    for _ in range(3):
        assert discovery._find_recording_for("missingxxxxx", rec) is None

    assert scans == 1


def test_saturated_negative_cache_invalidates_on_directory_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.setattr(discovery, "DISCOVERY_CACHE_MAX_ENTRIES", 2)
    rec = tmp_path / "recordings"
    for sid in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
        _write_recording(rec, sid)
    assert discovery._find_recording_for("newsessionxx", rec) is None

    created = _write_recording(rec, "newsessionxx")
    new_mtime = rec.stat().st_mtime_ns + 1_000_000_000
    os.utime(rec, ns=(new_mtime, new_mtime))

    assert discovery._find_recording_for("newsessionxx", rec) == created


def test_saturated_overflow_cache_is_lru_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "DISCOVERY_CACHE_MAX_ENTRIES", 2)
    rec = tmp_path / "recordings"
    for sid in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
        _write_recording(rec, sid)

    for sid in ("missing00001", "missing00002", "missing00003"):
        assert discovery._find_recording_for(sid, rec) is None

    _mtime, _index, saturated, overflow = discovery._recording_index[rec]
    assert saturated is True
    assert list(overflow) == ["missing00002", "missing00003"]
