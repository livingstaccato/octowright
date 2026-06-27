# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
import time
from pathlib import Path

from octowright import captures


def test_save_get_search_and_list_capture(tmp_path: Path) -> None:
    saved = captures.save_capture(
        kind="text",
        content="alpha\nEnter your alias\nomega",
        url="https://warp.undef.games/customize",
        title="Warp",
        instance_id="abc123",
        root=tmp_path,
        max_total_bytes=10_000,
        ttl_seconds=3600,
        preview_chars=5,
    )

    assert saved["truncated"] is True
    assert saved["preview"] == "alpha"
    assert saved["host"] == "warp.undef.games"

    sliced = captures.get_capture_slice(saved["capture_id"], offset=6, limit=5, root=tmp_path)
    assert sliced["content"] == "Enter"
    assert sliced["next_offset"] == 11

    found = captures.search_capture(saved["capture_id"], "alias", root=tmp_path, context_chars=8)
    assert found["count"] == 1
    assert "alias" in found["matches"][0]["context"]

    listed = captures.list_captures(root=tmp_path, instance_id="abc123")
    assert listed["count"] == 1
    assert listed["captures"][0]["capture_id"] == saved["capture_id"]


def test_cleanup_captures_prunes_by_age_and_size(tmp_path: Path) -> None:
    old = captures.save_capture(kind="text", content="old", root=tmp_path, max_total_bytes=10_000, ttl_seconds=3600)
    new = captures.save_capture(kind="text", content="x" * 100, root=tmp_path, max_total_bytes=10_000, ttl_seconds=3600)

    old_path = Path(old["path"])
    old_time = time.time() - 10_000
    os.utime(old_path, (old_time, old_time))

    dry = captures.cleanup_captures(root=tmp_path, ttl_seconds=100, max_total_bytes=10_000, apply=False)
    assert dry["eligible_count"] == 1
    assert old_path.exists()

    applied = captures.cleanup_captures(root=tmp_path, ttl_seconds=100, max_total_bytes=10_000, apply=True)
    assert applied["removed_count"] == 1
    assert not old_path.exists()
    assert Path(new["path"]).exists()

    size_prune = captures.cleanup_captures(root=tmp_path, ttl_seconds=3600, max_total_bytes=1, apply=True)
    assert size_prune["removed_count"] == 1


def test_capture_get_and_search_enforce_response_caps(tmp_path: Path) -> None:
    saved = captures.save_capture(
        kind="text",
        content="a" * 20_000,
        root=tmp_path,
        max_total_bytes=100_000,
        ttl_seconds=3600,
    )

    sliced = captures.get_capture_slice(saved["capture_id"], limit=1_000_000, root=tmp_path)
    assert len(sliced["content"]) == captures.MAX_SLICE_CHARS
    assert sliced["limit"] == captures.MAX_SLICE_CHARS
    assert sliced["truncated"] is True

    found = captures.search_capture(
        saved["capture_id"],
        "a",
        context_chars=1_000_000,
        limit=1_000_000,
        root=tmp_path,
    )
    assert found["count"] == captures.MAX_SEARCH_MATCHES
    assert all(len(match["context"]) <= (captures.MAX_SEARCH_CONTEXT_CHARS * 2) + 1 for match in found["matches"])


def test_save_capture_prunes_after_write_to_enforce_total_size(tmp_path: Path) -> None:
    first = captures.save_capture(
        kind="text",
        content="older" * 120,
        root=tmp_path,
        max_total_bytes=10_000,
        ttl_seconds=3600,
    )
    first_path = Path(first["path"])
    old_time = time.time() - 10
    os.utime(first_path, (old_time, old_time))

    second = captures.save_capture(
        kind="text",
        content="x" * 100,
        root=tmp_path,
        max_total_bytes=900,
        ttl_seconds=3600,
    )

    assert not first_path.exists()
    assert Path(second["path"]).exists()
    assert captures.cleanup_captures(root=tmp_path, ttl_seconds=3600, max_total_bytes=900)["eligible_count"] == 0


def test_storage_report_counts_known_roots(tmp_path: Path) -> None:
    recordings = tmp_path / "state" / "sessions"
    config = tmp_path / "config"
    cache = tmp_path / "cache" / "captures"
    (recordings / "videos").mkdir(parents=True)
    (config / "profiles").mkdir(parents=True)
    cache.mkdir(parents=True)
    (recordings / "a.jsonl").write_text("{}\n")
    (config / "profiles" / "profile.yaml").write_text("name: demo\n")
    (cache / "capture.json").write_text("{}")

    report = captures.storage_report(recordings_dir=recordings, config_dir=config, captures_dir=cache)

    assert report["recordings"]["files"] == 1
    assert report["profiles"]["files"] == 1
    assert report["captures"]["files"] == 1


def test_save_capture_does_not_follow_symlink_at_target(monkeypatch, tmp_path: Path) -> None:
    """A symlink at the capture destination must be replaced atomically, not followed."""
    import json as _json

    root = tmp_path / "captures"
    root.mkdir()
    sentinel = tmp_path / "outside.json"
    sentinel.write_text("KEEP", encoding="utf-8")
    target = root / "cap.json"
    target.symlink_to(sentinel)
    monkeypatch.setattr(captures, "_capture_path", lambda *a, **k: target)

    captures.save_capture(
        kind="text",
        content="hello",
        url="https://x.test",
        root=root,
        max_total_bytes=10_000,
        ttl_seconds=3600,
        preview_chars=10,
    )

    assert sentinel.read_text(encoding="utf-8") == "KEEP"  # outside file untouched
    assert not target.is_symlink()  # symlink replaced by a real file
    assert _json.loads(target.read_text(encoding="utf-8"))["content"] == "hello"
