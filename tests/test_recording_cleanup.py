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
