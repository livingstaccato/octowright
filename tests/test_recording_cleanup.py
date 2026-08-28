# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the recording-cleanup module + MCP tool + CLI command."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from octowright import recording_cleanup as rc


def _touch(path: Path, *, age_days: float, content: bytes = b"x") -> Path:
    """Create ``path`` with ``content`` and stamp its mtime to ``age_days`` ago."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    when = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
    os.utime(path, (when, when))
    return path


# ---------------------------------------------------------------------------
# find_stale_files
# ---------------------------------------------------------------------------


def test_find_stale_files_classifies_by_suffix_and_path(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    fixed_now = datetime.now(UTC)

    paths = {
        "recording": _touch(rec / "abc-run.jsonl", age_days=10),
        "screenshot": _touch(rec / "abc-shot.png", age_days=10),
        "trace": _touch(rec / "abc-trace.zip", age_days=10),
        "video": _touch(rec / "videos" / "deadbeef" / "page.webm", age_days=10),
        "video_path_wins": _touch(rec / "videos" / "deadbeef" / "thumb.png", age_days=10),
        "download": _touch(rec / "downloads" / "report.csv", age_days=10),
        "other": _touch(rec / "leftover.tmp", age_days=10),
    }

    stale = rc.find_stale_files(rec, days=1.0, now=fixed_now)
    by_path = {s.path: s for s in stale}

    assert by_path[paths["recording"]].kind == "recording"
    assert by_path[paths["screenshot"]].kind == "screenshot"
    assert by_path[paths["trace"]].kind == "trace"
    assert by_path[paths["video"]].kind == "video"
    # path-based rule wins: a .png under videos/ is still "video".
    assert by_path[paths["video_path_wins"]].kind == "video"
    # downloads classify as "other" but still appear.
    assert by_path[paths["download"]].kind == "other"
    assert by_path[paths["other"]].kind == "other"
    assert len(stale) == 7


def test_find_stale_files_skips_fresh_files(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    fixed_now = datetime.now(UTC)

    fresh = _touch(rec / "fresh.jsonl", age_days=0.5)
    old = _touch(rec / "old.jsonl", age_days=5)

    stale = rc.find_stale_files(rec, days=1.0, now=fixed_now)
    paths = {s.path for s in stale}
    assert old in paths
    assert fresh not in paths
    # Age math should be ~5 days for the old one.
    assert any(4.9 < s.age_days < 5.1 for s in stale)


def test_find_stale_files_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert rc.find_stale_files(tmp_path / "nope", days=1.0) == []


# ---------------------------------------------------------------------------
# cleanup_stale
# ---------------------------------------------------------------------------


def test_cleanup_stale_dry_run_keeps_files(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    a = _touch(rec / "a.jsonl", age_days=10, content=b"hello")
    b = _touch(rec / "b.png", age_days=10, content=b"world!")

    stale = rc.find_stale_files(rec, days=1.0)
    summary = rc.cleanup_stale(stale, dry_run=True)

    assert a.exists()
    assert b.exists()
    assert summary["dry_run"] is True
    assert summary["removed_count"] == 0
    assert summary["removed_bytes"] == 0
    assert summary["errors"] == []


def test_cleanup_stale_apply_deletes_and_counts(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    a = _touch(rec / "a.jsonl", age_days=10, content=b"hello")
    b = _touch(rec / "b.png", age_days=10, content=b"world!")
    expected_bytes = a.stat().st_size + b.stat().st_size

    stale = rc.find_stale_files(rec, days=1.0)
    summary = rc.cleanup_stale(stale, dry_run=False)

    assert not a.exists()
    assert not b.exists()
    assert summary["dry_run"] is False
    assert summary["removed_count"] == 2
    assert summary["removed_bytes"] == expected_bytes
    assert summary["errors"] == []


def test_cleanup_stale_evicts_artifact_cache_for_recording(tmp_path: Path) -> None:
    """Recording deletion should drop any cached artefact rows for that JSONL."""
    from octowright.http.session_artifacts import SessionArtifactCache

    rec = tmp_path / "recordings"
    rec.mkdir()
    jsonl = _touch(
        rec / "stale.jsonl",
        age_days=10,
        content=b'{"action": "console", "level": "log", "text": "ghost"}\n',
    )

    cache = SessionArtifactCache()
    cache.write_event_indexes(jsonl)
    assert str(jsonl) in cache._console_index_cache

    # Patch the module-level singleton used by cleanup_stale.
    import octowright.http.session_artifacts as sa_mod

    sa_mod.session_artifact_cache._console_index_cache = cache._console_index_cache
    sa_mod.session_artifact_cache._artifact_cache = cache._artifact_cache
    sa_mod.session_artifact_cache._report_cache = cache._report_cache
    sa_mod.session_artifact_cache._downloads_index_cache = cache._downloads_index_cache

    stale = rc.find_stale_files(rec, days=1.0)
    rc.cleanup_stale(stale, dry_run=False)

    assert not jsonl.exists()
    assert str(jsonl) not in sa_mod.session_artifact_cache._console_index_cache


def test_cleanup_stale_invalidates_recording_index(tmp_path: Path) -> None:
    """Deleting a JSONL must drop it from the in-memory recording-id index."""
    from octowright.http.discovery import (
        _find_recording_for,
        _recording_index,
        invalidate_recording_index,
    )

    invalidate_recording_index()
    rec = tmp_path / "recordings"
    rec.mkdir()
    jsonl = _touch(
        rec / "20260101T000000Z-chromium-stale1234abcd.jsonl",
        age_days=10,
        content=b'{"action": "launch"}\n',
    )

    # Prime the index with a lookup. The index is now a (dir_mtime, {sid: path}) tuple.
    found = _find_recording_for("stale1234abcd", rec)
    assert found == jsonl
    assert "stale1234abcd" in _recording_index[rec][1]

    stale = rc.find_stale_files(rec, days=1.0)
    rc.cleanup_stale(stale, dry_run=False)

    assert not jsonl.exists()
    # Index for that dir should have been dropped.
    assert rec not in _recording_index
    # Subsequent lookup rebuilds and finds nothing.
    assert _find_recording_for("stale1234abcd", rec) is None


def test_find_recording_for_caches_lookup(tmp_path: Path) -> None:
    """Second lookup of the same id should hit the in-memory index."""
    import os

    from octowright.http.discovery import (
        _find_recording_for,
        _recording_index,
        invalidate_recording_index,
    )

    invalidate_recording_index()
    rec = tmp_path / "recordings"
    rec.mkdir()
    jsonl = rec / "20260101T000000Z-chromium-cachedidwxyz.jsonl"
    jsonl.write_text('{"action": "launch"}\n', encoding="utf-8")

    assert _find_recording_for("cachedidwxyz", rec) == jsonl
    assert "cachedidwxyz" in _recording_index[rec][1]

    # Removing the entry from the inner dict simulates corruption. Rebuild
    # is gated on dir mtime now — bump the dir mtime so the rebuild fires.
    _recording_index[rec][1].pop("cachedidwxyz")
    new_mtime = rec.stat().st_mtime_ns + 1_000_000_000
    os.utime(rec, ns=(new_mtime, new_mtime))
    assert _find_recording_for("cachedidwxyz", rec) == jsonl
    assert "cachedidwxyz" in _recording_index[rec][1]


def test_cleanup_stale_video_dir_pruned_when_empty(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    vid_dir = rec / "videos" / "deadbeef"
    vid = _touch(vid_dir / "page.webm", age_days=10)

    stale = rc.find_stale_files(rec, days=1.0)
    rc.cleanup_stale(stale, dry_run=False)

    assert not vid.exists()
    # Empty subdir under videos/ should be best-effort removed.
    assert not vid_dir.exists()
    # videos/ itself stays.
    assert (rec / "videos").exists()


def test_cleanup_stale_collects_unlink_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    good = _touch(rec / "good.jsonl", age_days=10, content=b"abc")
    bad = _touch(rec / "bad.jsonl", age_days=10, content=b"xyz")

    real_unlink = Path.unlink

    def fake_unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if self == bad:
            raise PermissionError("denied")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    stale = rc.find_stale_files(rec, days=1.0)
    summary = rc.cleanup_stale(stale, dry_run=False)

    assert not good.exists()  # good one still cleaned
    assert bad.exists()  # bad one still around
    assert summary["removed_count"] == 1
    assert len(summary["errors"]) == 1
    assert summary["errors"][0]["path"] == str(bad)
    assert "denied" in summary["errors"][0]["error"]


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------


def test_mcp_recordings_cleanup_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    _touch(rec / "a.jsonl", age_days=10)
    _touch(rec / "b.png", age_days=10)
    _touch(rec / "c.zip", age_days=10)
    _touch(rec / "videos" / "deadbeef" / "page.webm", age_days=10)
    _touch(rec / "fresh.jsonl", age_days=0.1)

    # Patch RECORDINGS_DIR everywhere the tool resolves it from.
    from octowright import defaults as _defaults

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)

    from octowright.server.macros import recordings_cleanup

    result = recordings_cleanup(days=1.0, dry_run=True)

    assert result["dry_run"] is True
    assert result["recordings_dir"] == str(rec)
    assert result["found"] == 4
    assert result["would_remove"] == 4
    assert result["removed"] == 0
    assert result["freed_bytes"] > 0
    assert result["by_kind"] == {
        "recording": 1,
        "screenshot": 1,
        "video": 1,
        "trace": 1,
        "other": 0,
    }
    assert result["errors"] == []
    # Files still present (dry-run).
    assert (rec / "a.jsonl").exists()


def test_mcp_recordings_cleanup_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    _touch(rec / "a.jsonl", age_days=10)
    _touch(rec / "b.png", age_days=10)

    from octowright import defaults as _defaults

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)

    from octowright.server.macros import recordings_cleanup

    result = recordings_cleanup(days=1.0, dry_run=False)
    assert result["removed"] == 2
    assert result["would_remove"] == 0
    assert not (rec / "a.jsonl").exists()
    assert not (rec / "b.png").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_cleanup_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    _touch(rec / "a.jsonl", age_days=10)
    _touch(rec / "b.png", age_days=10)

    from octowright import defaults as _defaults

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)

    from octowright.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["cleanup", "--days", "1"])
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    assert "recording" in result.output
    assert "screenshot" in result.output
    # Files still there.
    assert (rec / "a.jsonl").exists()
    assert (rec / "b.png").exists()


def test_cli_cleanup_apply_deletes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = tmp_path / "recordings"
    rec.mkdir()
    a = _touch(rec / "a.jsonl", age_days=10)
    b = _touch(rec / "b.png", age_days=10)

    from octowright import defaults as _defaults

    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)

    from octowright.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["cleanup", "--days", "1", "--apply"])
    assert result.exit_code == 0, result.output
    assert "removed 2" in result.output
    assert not a.exists()
    assert not b.exists()


# ---------------------------------------------------------------------------
# The artifact store must survive the sweep
#
# `ArtifactStore` roots itself at `<recordings_dir>/artifacts`, so macro
# artifacts have always lived inside the tree this sweep walks -- and it walked
# them. A 30-day cleanup deleted artifact.json with its configured critical
# points, every run bundle, verification.json, summary.md and any exported CLI,
# reporting them as `other` in the per-kind breakdown that exists precisely so
# an operator can see what they are about to free.
# ---------------------------------------------------------------------------


def test_the_artifact_store_is_never_swept(tmp_path: Path) -> None:
    """Age says nothing about whether a macro artifact is disposable.

    A recording is a byproduct; a critical point is something a person wrote.
    The artifact whose files stop being touched is the stable one that keeps
    passing -- exactly the one worth keeping.
    """
    root = tmp_path / "recordings"
    artifact_files = [
        _touch(root / "artifacts" / "macros" / "login" / "artifact.json", age_days=90),
        _touch(root / "artifacts" / "macros" / "login" / "runs" / "run_0001" / "result.json", age_days=90),
        _touch(root / "artifacts" / "macros" / "login" / "runs" / "run_0001" / "summary.md", age_days=90),
        _touch(
            root / "artifacts" / "macros" / "login" / "runs" / "run_0001" / "verification.json",
            age_days=90,
        ),
        _touch(root / "artifacts" / "macros" / "login" / "exports" / "login.py", age_days=90),
    ]

    stale = rc.find_stale_files(root, days=30)

    assert stale == []
    for path in artifact_files:
        assert path.exists()


def test_recordings_beside_the_artifact_store_are_still_swept(tmp_path: Path) -> None:
    """The exclusion is a subtree, not a blanket -- ordinary recordings still go."""
    root = tmp_path / "recordings"
    _touch(root / "artifacts" / "macros" / "login" / "artifact.json", age_days=90)
    recording = _touch(root / "20260101T000000Z-chromium-abc.jsonl", age_days=90)
    screenshot = _touch(root / "shots" / "before.png", age_days=90)

    swept = {s.path for s in rc.find_stale_files(root, days=30)}

    assert swept == {recording, screenshot}


def test_the_frame_cache_is_deliberately_still_swept(tmp_path: Path) -> None:
    """`.frame-cache` is regenerable, so sweeping it is the point.

    Pinned so a future "preserve everything that looks internal" change has to
    argue with a test rather than quietly stop reclaiming cache space.
    """
    root = tmp_path / "recordings"
    cached = _touch(root / ".frame-cache" / "session-1" / "0001.png", age_days=90)

    assert [s.path for s in rc.find_stale_files(root, days=30)] == [cached]


def test_a_directory_merely_named_artifacts_deeper_down_is_not_protected(
    tmp_path: Path,
) -> None:
    """Only the artifact store at the root is preserved.

    The check is anchored on the first path component rather than matching the
    name anywhere, so a session that happens to write into a folder called
    `artifacts` does not get itself exempted from cleanup.
    """
    root = tmp_path / "recordings"
    nested = _touch(root / "session-1" / "artifacts" / "blob.bin", age_days=90)

    assert [s.path for s in rc.find_stale_files(root, days=30)] == [nested]
