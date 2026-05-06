# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from octowright.demos.export import build_tutorial_export
from octowright.demos.indexer import build_demo_index, build_manifest_row
from octowright.demos.models import DemoBundle


def test_build_manifest_row_preserves_declared_and_existing_artifacts(tmp_path: Path) -> None:
    bundle = DemoBundle(
        id="cross-engine-trio",
        title="Cross Engine Trio",
        summary="Three engines.",
        hero=True,
        audiences=["evaluators"],
        tags=["hero", "engines"],
        engines=["chromium", "firefox", "webkit"],
        roles=["player"],
        scenarios=["scenario/cross-engine.yaml"],
        replay_artifacts=["artifacts/replay.jsonl", "artifacts/missing.jsonl"],
        video_artifacts=["artifacts/demo.mp4"],
        regen_command="uv run python scripts/demos/record_demo.py cross-engine-trio",
        tutorial_export="exports/cross-engine-trio.md",
        root=tmp_path,
    )
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "replay.jsonl").write_text("{}", encoding="utf-8")

    row = build_manifest_row(bundle)

    assert row["id"] == "cross-engine-trio"
    assert row["hero"] is True
    assert row["artifacts"] == {
        "replay": {
            "declared_count": 2,
            "existing_count": 1,
            "declared_paths": ["artifacts/replay.jsonl", "artifacts/missing.jsonl"],
            "existing_paths": ["artifacts/replay.jsonl"],
        },
        "video": {
            "declared_count": 1,
            "existing_count": 0,
            "declared_paths": ["artifacts/demo.mp4"],
            "existing_paths": [],
        },
    }


def test_build_demo_index_renders_rich_hero_first_content(tmp_path: Path) -> None:
    hero = DemoBundle(
        id="hero-demo",
        title="Hero Demo",
        summary="Primary walkthrough.",
        hero=True,
        audiences=["evaluators", "sales"],
        tags=["hero", "engines"],
        replay_artifacts=["artifacts/hero-replay.jsonl"],
        video_artifacts=["artifacts/hero-demo.mp4"],
        regen_command="uv run hero",
        root=tmp_path / "hero",
    )
    support = DemoBundle(
        id="support-demo",
        title="Support Demo",
        summary="Secondary walkthrough.",
        hero=False,
        audiences=["support"],
        tags=["training"],
        replay_artifacts=["artifacts/support-replay.jsonl"],
        regen_command="uv run support",
        root=tmp_path / "support",
    )
    (hero.root / "artifacts").mkdir(parents=True)
    (hero.root / "artifacts" / "hero-replay.jsonl").write_text("{}", encoding="utf-8")
    hero_replay_mtime = datetime(2026, 5, 5, 12, 34, 56, tzinfo=UTC).timestamp()
    (hero.root / "artifacts" / "hero-demo.mp4").write_text("video", encoding="utf-8")
    (hero.root / "artifacts" / "hero-demo.mp4").touch()
    (hero.root / "artifacts" / "hero-replay.jsonl").touch()
    (hero.root / "artifacts" / "hero-replay.jsonl").chmod(0o644)
    (hero.root / "artifacts" / "hero-demo.mp4").chmod(0o644)
    os.utime(hero.root / "artifacts" / "hero-replay.jsonl", (hero_replay_mtime, hero_replay_mtime))
    os.utime(hero.root / "artifacts" / "hero-demo.mp4", (hero_replay_mtime, hero_replay_mtime))
    (support.root / "artifacts").mkdir(parents=True)

    markdown = build_demo_index([support, hero])

    assert markdown.index("## Hero Demos") < markdown.index("## Full Library")
    assert markdown.index("Hero Demo") < markdown.index("Support Demo")
    assert "`hero-demo`" in markdown
    assert "Primary walkthrough." in markdown
    assert "Tags: `hero`, `engines`" in markdown
    assert "Audiences: `evaluators`, `sales`" in markdown
    assert "Regen: `uv run hero`" in markdown
    assert "Artifacts: replay 1/1, video 1/1" in markdown
    assert "Generation status: generated" in markdown
    assert "Generation status: not-generated" in markdown
    assert "Last generated: 2026-05-05 12:34:56 UTC" in markdown
    assert "Last generated: n/a" in markdown
    assert (
        "Replay artifacts: existing `artifacts/hero-replay.jsonl`; declared `artifacts/hero-replay.jsonl`" in markdown
    )
    assert "Video artifacts: existing `artifacts/hero-demo.mp4`; declared `artifacts/hero-demo.mp4`" in markdown
    assert "Replay artifacts: declared `artifacts/support-replay.jsonl`" in markdown
    assert "Video artifacts: none declared" in markdown
    assert "Supporting demos appear here for the complete non-hero catalog; hero demos are featured above." in markdown
    assert "Artifacts: replay 0/1, video 0/0" in markdown


def test_build_tutorial_export_includes_hero_assets(tmp_path: Path) -> None:
    bundle = DemoBundle(
        id="hero-demo",
        title="Hero Demo",
        summary="Primary walkthrough.",
        hero=True,
        audiences=["evaluators"],
        tags=["hero"],
        replay_artifacts=["artifacts/replay.jsonl"],
        video_artifacts=["artifacts/demo.mp4", "artifacts/poster.png"],
        regen_command="uv run python scripts/demos/record_demo.py hero-demo",
        tutorial_export="exports/hero-demo.md",
        root=tmp_path,
    )
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "poster.png").write_bytes(b"png")
    (tmp_path / "artifacts" / "demo.mp4").write_bytes(b"mp4")
    (tmp_path / "artifacts" / "replay.jsonl").write_text("{}", encoding="utf-8")

    payload = build_tutorial_export(bundle)

    assert payload == {
        "id": "hero-demo",
        "title": "Hero Demo",
        "summary": "Primary walkthrough.",
        "hero": True,
        "tutorial_export": "exports/hero-demo.md",
        "regen_command": "uv run python scripts/demos/record_demo.py hero-demo",
        "assets": {
            "video": ["artifacts/demo.mp4", "artifacts/poster.png"],
            "replay": ["artifacts/replay.jsonl"],
        },
    }


def test_build_tutorial_export_can_override_emitted_path(tmp_path: Path) -> None:
    bundle = DemoBundle(
        id="hero-demo",
        title="Hero Demo",
        tutorial_export="exports/hero-demo.md",
        root=tmp_path,
    )

    payload = build_tutorial_export(bundle, tutorial_export_path=tmp_path / "exports" / "hero-demo.json")

    assert payload["tutorial_export"] == "exports/hero-demo.json"
