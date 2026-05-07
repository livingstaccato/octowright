# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.demos.models import DemoBundle, DemoPresentationConfig
from octowright.demos.rendering import render_bundle_video
from octowright.demos.runtime import record_demo_bundle
from octowright.scenarios_pool import LiveScenario
from tests.test_demos_runtime import _FakePool, _FakeScenarioPool, _write_bundle_layout, _write_duo_bundle_layout


def _patch_runtime_recording(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    render_summary: dict[str, object] | None = None,
) -> None:
    def _fake_prepare_scenario(bundle_obj, scenario):
        runnable = scenario
        runnable._log_dir = bundle_obj.root / "artifacts" / "raw"
        runnable._log_dir.mkdir(parents=True, exist_ok=True)
        return runnable

    monkeypatch.setattr("octowright.demos.runtime.BrowserPool", _FakePool)
    monkeypatch.setattr("octowright.demos.runtime.ScenarioPool", _FakeScenarioPool)
    monkeypatch.setattr("octowright.demos.runtime._repo_root", lambda _: tmp_path)
    monkeypatch.setattr("octowright.demos.runtime._prepare_scenario", _fake_prepare_scenario)
    monkeypatch.setattr(
        "octowright.demos.runtime.write_exports",
        lambda replay_path: (
            replay_path.with_suffix(".py").write_text("python", encoding="utf-8")
            or replay_path.with_suffix(".ts").write_text("ts", encoding="utf-8")
        ),
    )
    monkeypatch.setattr(
        "octowright.demos.runtime.render_bundle_video",
        _render_summary_factory(render_summary),
    )
    monkeypatch.setattr(
        "octowright.demos.runtime.write_artifact_manifest",
        lambda *args, **kwargs: ((kwargs["video_path"].parent / "manifest.json").write_text("{}", encoding="utf-8")),
    )


def _render_summary_factory(render_summary: dict[str, object] | None = None):
    def _render(bundle_obj, live, close_results, *, video_path: Path, poster_path: Path) -> dict[str, object]:
        _ = (bundle_obj, live, close_results)
        video_path.write_bytes(b"video")
        poster_path.write_bytes(b"poster")
        return render_summary or {"mode": "single", "canvas_width": 800, "canvas_height": 600, "panes": []}

    return _render


@pytest.mark.asyncio
async def test_record_demo_bundle_applies_intro_and_outro_holds(monkeypatch, tmp_path: Path) -> None:
    bundle = _write_bundle_layout(tmp_path)
    bundle.presentation.timing.intro_ms = 250
    bundle.presentation.timing.outro_ms = 1250

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    _patch_runtime_recording(monkeypatch, tmp_path)
    monkeypatch.setattr("octowright.demos.runtime.asyncio.sleep", _fake_sleep)

    await record_demo_bundle(bundle)

    assert sleeps == [0.25, 1.25]


@pytest.mark.asyncio
async def test_record_demo_bundle_writes_supporting_sync_assets(monkeypatch, tmp_path: Path) -> None:
    bundle = _write_duo_bundle_layout(tmp_path)
    bundle.presentation.mode = "sync-multi"

    _patch_runtime_recording(
        monkeypatch,
        tmp_path,
        render_summary={
            "mode": "sync-multi",
            "canvas_width": 1920,
            "canvas_height": 540,
            "panes": [],
            "supporting_videos": [
                {
                    "id": "player",
                    "path": "artifacts/supporting/player.mp4",
                    "poster_path": "artifacts/supporting/player.png",
                },
                {
                    "id": "monitor",
                    "path": "artifacts/supporting/monitor.mp4",
                    "poster_path": "artifacts/supporting/monitor.png",
                },
            ],
        },
    )

    result = await record_demo_bundle(bundle)

    assert "supporting_videos" in result
    assert result["supporting_videos"] == [
        {
            "id": "player",
            "path": "artifacts/supporting/player.mp4",
            "poster_path": "artifacts/supporting/player.png",
        },
        {
            "id": "monitor",
            "path": "artifacts/supporting/monitor.mp4",
            "poster_path": "artifacts/supporting/monitor.png",
        },
    ]


@pytest.mark.asyncio
async def test_record_demo_bundle_composes_video_for_multi_browser_bundles(monkeypatch, tmp_path: Path) -> None:
    bundle = _write_duo_bundle_layout(tmp_path)

    def _fake_prepare_scenario(bundle_obj, scenario):
        runnable = scenario
        runnable._log_dir = bundle_obj.root / "artifacts" / "raw"
        runnable._log_dir.mkdir(parents=True, exist_ok=True)
        return runnable

    composed: dict[str, object] = {}
    extracted: dict[str, object] = {}

    monkeypatch.setattr("octowright.demos.runtime.BrowserPool", _FakePool)
    monkeypatch.setattr("octowright.demos.runtime.ScenarioPool", _FakeScenarioPool)
    monkeypatch.setattr("octowright.demos.runtime._repo_root", lambda _: tmp_path)
    monkeypatch.setattr("octowright.demos.runtime._prepare_scenario", _fake_prepare_scenario)
    monkeypatch.setattr(
        "octowright.demos.runtime.write_exports",
        lambda replay_path: (
            replay_path.with_suffix(".py").write_text("python", encoding="utf-8")
            or replay_path.with_suffix(".ts").write_text("ts", encoding="utf-8")
        ),
    )

    def _fake_render(bundle_obj, live, close_results, *, video_path: Path, poster_path: Path) -> dict[str, object]:
        composed.update(
            {
                "target": str(video_path),
                "poster_target": str(poster_path),
                "bundle": bundle_obj.id,
                "participant_count": len(live.participants),
            }
        )
        return _render_summary_factory(
            {
                "mode": "grid",
                "canvas_width": 1920,
                "canvas_height": 540,
                "panes": [],
                "overlay": {"title": "Role Based Duo"},
            }
        )(
            bundle_obj,
            live,
            close_results,
            video_path=video_path,
            poster_path=poster_path,
        )

    monkeypatch.setattr("octowright.demos.runtime.render_bundle_video", _fake_render)
    monkeypatch.setattr(
        "octowright.demos.runtime.write_artifact_manifest",
        lambda *args, **kwargs: (
            extracted.update({"video": str(kwargs["video_path"]), "target": str(kwargs["poster_path"])})
            or (kwargs["video_path"].parent / "manifest.json").write_text("{}", encoding="utf-8")
        ),
    )

    result = await record_demo_bundle(bundle)

    assert result["bundle_id"] == "role-based-duo"
    assert composed["bundle"] == "role-based-duo"
    assert composed["participant_count"] == 2
    assert extracted["video"] == str(bundle.root / "artifacts" / "demo.mp4")
    assert extracted["target"] == str(bundle.root / "artifacts" / "poster.png")


def test_render_bundle_video_writes_unique_supporting_assets_for_duplicate_roles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = DemoBundle(id="cross-engine-trio", title="Cross Engine Trio", root=tmp_path)
    bundle.presentation = DemoPresentationConfig(mode="sync-multi")

    source_videos = [tmp_path / "chromium.webm", tmp_path / "firefox.webm", tmp_path / "webkit.webm"]
    for path in source_videos:
        path.write_bytes(path.stem.encode("utf-8"))

    live = LiveScenario(
        scenario_id="cross-engine-live",
        name="cross-engine",
        spec=None,
        participants=[
            {"instance_id": "iid-1", "persona": "cx-chromium", "role": "player", "kind": "chromium"},
            {"instance_id": "iid-2", "persona": "cx-firefox", "role": "player", "kind": "firefox"},
            {"instance_id": "iid-3", "persona": "cx-webkit", "role": "player", "kind": "webkit"},
        ],
    )
    close_results = {
        "iid-1": {"video_path": str(source_videos[0])},
        "iid-2": {"video_path": str(source_videos[1])},
        "iid-3": {"video_path": str(source_videos[2])},
    }
    rendered_targets: list[tuple[str, str]] = []

    def _fake_supporting_video(source_path: Path, target_path: Path, *, poster_path: Path) -> dict[str, str]:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_path.read_bytes())
        poster_path.write_bytes(b"poster")
        rendered_targets.append((target_path.name, poster_path.name))
        return {"path": str(target_path), "poster_path": str(poster_path)}

    monkeypatch.setattr("octowright.demos.rendering.render_supporting_video", _fake_supporting_video)
    monkeypatch.setattr(
        "octowright.demos.rendering.compose_video_grid",
        lambda sources, target, **kwargs: target.write_bytes(b"video") or target,
    )
    monkeypatch.setattr(
        "octowright.demos.rendering.extract_frame",
        lambda source, target: target.write_bytes(b"poster") or target,
    )
    monkeypatch.setattr(
        "octowright.demos.rendering.probe_video",
        lambda _: {"width": 1920, "height": 360, "duration_seconds": 2.0},
    )

    summary = render_bundle_video(
        bundle,
        live,
        close_results,
        video_path=tmp_path / "demo.mp4",
        poster_path=tmp_path / "poster.png",
    )

    assert [item["id"] for item in summary["supporting_videos"]] == ["player", "player-2", "player-3"]
    assert [item["path"] for item in summary["supporting_videos"]] == [
        str(tmp_path / "supporting" / "player.mp4"),
        str(tmp_path / "supporting" / "player-2.mp4"),
        str(tmp_path / "supporting" / "player-3.mp4"),
    ]
    assert rendered_targets == [
        ("player.mp4", "player.png"),
        ("player-2.mp4", "player-2.png"),
        ("player-3.mp4", "player-3.png"),
    ]
