# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The assembled closed-session listing and its per-directory snapshot.

``_summarise_recording_cached``'s LRU cannot serve this listing: the walk is
sequential over every recording, so a corpus larger than the LRU evicts its own
earliest entries and the next request misses on everything but the tail.
Measured on a real 10,177-recording directory, ``/api/sessions`` re-opened
~9,600 files and took 2.8s on every call, warm or cold. These tests pin the
snapshot that replaced it, and the response cap that stopped shipping 2.7 MB
for the twenty rows the dashboard renders.

Directory mtimes are set explicitly with ``os.utime`` rather than relying on a
write to bump them -- filesystem timestamp resolution is not ours to assume,
and an assertion against it would be an assertion against unmeasured timing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from octowright.http import discovery


@pytest.fixture(autouse=True)
def _clear_listing_cache() -> None:
    discovery.invalidate_closed_list()


def _write_recording(rec_dir: Path, instance_id: str, *, started: str = "2026-01-01T00:00:00Z") -> Path:
    rec_dir.mkdir(parents=True, exist_ok=True)
    path = rec_dir / f"{started[:4]}0101T000000Z-chromium-{instance_id}.jsonl"
    path.write_text(
        json.dumps({"action": "launch", "kind": "chromium", "ts": started}) + "\n",
        encoding="utf-8",
    )
    return path


def _bump_dir_mtime(directory: Path) -> None:
    """Force a distinct directory mtime, independent of fs timestamp resolution."""
    stat = directory.stat()
    os.utime(directory, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every recording ``_summarise_recording`` actually opens."""
    seen: list[Path] = []
    real = discovery._summarise_recording

    def counting(jsonl_path: Path):  # type: ignore[no-untyped-def]
        seen.append(jsonl_path)
        return real(jsonl_path)

    monkeypatch.setattr(discovery, "_summarise_recording", counting)
    return seen


def test_listing_parses_once_and_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second call opens no files at all -- the regression that mattered."""
    rec = tmp_path / "recordings"
    for i in range(5):
        _write_recording(rec, f"aaaaaaaa000{i}")
    seen = _count_parses(monkeypatch)

    first = discovery._summaries_for(rec)
    assert len(seen) == 5
    seen.clear()

    second = discovery._summaries_for(rec)
    assert seen == [], "a warm listing must not touch the filesystem"
    assert second is first


def test_rebuild_reparses_only_the_new_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding one recording must cost one read, not a whole directory."""
    rec = tmp_path / "recordings"
    for i in range(5):
        _write_recording(rec, f"bbbbbbbb000{i}")
    discovery._summaries_for(rec)

    seen = _count_parses(monkeypatch)
    added = _write_recording(rec, "cccccccc9999")
    _bump_dir_mtime(rec)

    rebuilt = discovery._summaries_for(rec)
    assert seen == [added], "unchanged recordings must be carried forward"
    assert len(rebuilt) == 6


def test_removed_recording_leaves_the_listing(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    keep = _write_recording(rec, "dddddddd0001")
    drop = _write_recording(rec, "dddddddd0002")
    assert len(discovery._summaries_for(rec)) == 2

    drop.unlink()
    _bump_dir_mtime(rec)
    remaining = discovery._summaries_for(rec)
    assert [s["log_path"] for s in remaining] == [str(keep)]


def test_invalidate_closed_list_forces_a_reread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = tmp_path / "recordings"
    _write_recording(rec, "eeeeeeee0001")
    discovery._summaries_for(rec)

    seen = _count_parses(monkeypatch)
    discovery.invalidate_closed_list(rec)
    discovery._summaries_for(rec)
    assert len(seen) == 1


def test_snapshot_ceiling_falls_back_to_an_uncached_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the ceiling the listing still works, it just stops being cached."""
    rec = tmp_path / "recordings"
    for i in range(3):
        _write_recording(rec, f"ffffffff000{i}")
    monkeypatch.setenv("OCTOWRIGHT_SESSION_LIST_SNAPSHOT_MAX", "2")

    seen = _count_parses(monkeypatch)
    assert len(discovery._summaries_for(rec)) == 3
    assert len(seen) == 3
    seen.clear()
    assert len(discovery._summaries_for(rec)) == 3
    assert len(seen) == 3, "above the ceiling nothing is retained"


def test_non_positive_ceiling_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw in ("0", "-1", "banana", ""):
        monkeypatch.setenv("OCTOWRIGHT_SESSION_LIST_SNAPSHOT_MAX", raw)
        assert discovery._summary_snapshot_limit() == discovery.SESSION_LIST_SNAPSHOT_MAX


def test_closed_sessions_reports_total_beyond_the_limit(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    for i in range(7):
        _write_recording(rec, f"99999999000{i}")

    rows, total = discovery._closed_sessions(rec, set(), limit=3)
    assert len(rows) == 3
    assert total == 7


def test_closed_sessions_excludes_live_recordings(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    live = _write_recording(rec, "1111111a0001")
    _write_recording(rec, "1111111a0002")

    rows, total = discovery._closed_sessions(rec, {str(live)})
    assert total == 1
    assert [s["log_path"] for s in rows] == [str(rec / "20260101T000000Z-chromium-1111111a0002.jsonl")]


def test_closed_sessions_orders_most_recent_first(tmp_path: Path) -> None:
    rec = tmp_path / "recordings"
    _write_recording(rec, "2222222a0001", started="2026-01-01T00:00:00Z")
    _write_recording(rec, "2222222a0002", started="2027-01-01T00:00:00Z")

    rows, _ = discovery._closed_sessions(rec, set())
    assert [s["started_at"] for s in rows] == ["2027-01-01T00:00:00Z", "2026-01-01T00:00:00Z"]
