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
