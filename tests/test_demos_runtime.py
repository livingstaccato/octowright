# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.demos.models import DemoBundle, DemoMacroRun, DemoPresentationConfig, DemoRecordingConfig
from octowright.demos.presentation_profiles import select_render_plan
from octowright.demos.rendering import render_bundle_video
from octowright.demos.runtime import record_demo_bundle
from octowright.scenarios_pool import LiveScenario


class _FakeSession:
    def __init__(self, instance_id: str, video_path: Path) -> None:
        self.instance_id = instance_id
        self._video_path = video_path
        self.page = self

    async def screenshot(self, path: str | Path = "", scale: str | None = None) -> Path:
        _ = scale
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"poster")
        return target


class _FakePool:
    def __init__(self) -> None:
        self._sessions: dict[str, _FakeSession] = {}

    def get(self, instance_id: str) -> _FakeSession:
        return self._sessions[instance_id]

    async def close(self, instance_id: str) -> dict[str, object]:
        return {"video_path": str(self._sessions[instance_id]._video_path)}

    async def shutdown(self) -> None:
        return None


class _FakeScenarioPool:
    def __init__(self) -> None:
        self._live: dict[str, object] = {}

    async def start(self, *, spec, browser_pool):
        participants = []
        for index, participant in enumerate(spec.participants, start=1):
            instance_id = f"iid-{index}"
            log_path = spec._log_dir / f"{instance_id}.jsonl"
            log_path.write_text(json.dumps({"ts": f"2026-05-06T00:00:0{index}Z", "action": "navigate"}) + "\n")
            video_path = spec._log_dir / f"{instance_id}.webm"
            video_path.write_bytes(b"video")
            browser_pool._sessions[instance_id] = _FakeSession(instance_id, video_path)
            participants.append(
                {
                    "instance_id": instance_id,
                    "persona": participant.persona,
                    "role": participant.role,
                    "kind": participant.kind,
                    "log_path": str(log_path),
                }
            )
        live = type("Live", (), {"scenario_id": "demo-live", "participants": participants, "spec": spec})()
        self._live[live.scenario_id] = live
        return live

    async def run_macro(self, **_: object) -> dict[str, object]:
        return {"results": [{"instance_id": "iid-1", "ok": True}]}


def _write_bundle_layout(root: Path) -> DemoBundle:
    repo_root = root
    bundle_root = repo_root / "demo" / "bundles" / "alpha-demo"
    scenario_path = repo_root / "examples" / "scenarios" / "alpha.yaml"
    macro_path = repo_root / "examples" / "macros" / "alpha.json"
    bundle_root.mkdir(parents=True, exist_ok=True)
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    macro_path.parent.mkdir(parents=True, exist_ok=True)
    (bundle_root / "seed").mkdir(parents=True, exist_ok=True)
    (bundle_root / "seed" / "stage.html").write_text("<html><body>stage</body></html>", encoding="utf-8")
    scenario_path.write_text(
        "name: alpha\nparticipants:\n  - persona: alpha\n    kind: webkit\n    role: player\n    url: about:blank\n",
        encoding="utf-8",
    )
    macro_path.write_text('{"name":"alpha","actions":[]}', encoding="utf-8")
    return DemoBundle(
        id="alpha-demo",
        title="Alpha Demo",
        scenarios=["examples/scenarios/alpha.yaml"],
        macro_refs=["examples/macros/alpha.json"],
        seed_refs=["seed/stage.html"],
        replay_artifacts=["artifacts/replay.jsonl", "artifacts/replay-roundtrip.jsonl"],
        video_artifacts=["artifacts/demo.mp4", "artifacts/poster.png"],
        recording=DemoRecordingConfig(
            primary_role="player",
            default_seed="seed/stage.html",
            macros=[DemoMacroRun(name="alpha", role="player")],
            extras=["replay-roundtrip"],
        ),
        root=bundle_root,
    )


def _write_duo_bundle_layout(root: Path) -> DemoBundle:
    repo_root = root
    bundle_root = repo_root / "demo" / "bundles" / "role-based-duo"
    scenario_path = repo_root / "examples" / "scenarios" / "duo.yaml"
    macro_path = repo_root / "examples" / "macros" / "alpha.json"
    bundle_root.mkdir(parents=True, exist_ok=True)
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    macro_path.parent.mkdir(parents=True, exist_ok=True)
    (bundle_root / "seed").mkdir(parents=True, exist_ok=True)
    (bundle_root / "seed" / "player.html").write_text("<html><body>player</body></html>", encoding="utf-8")
    (bundle_root / "seed" / "monitor.html").write_text("<html><body>monitor</body></html>", encoding="utf-8")
    scenario_path.write_text(
        (
            "name: duo\nparticipants:\n"
            "  - persona: player\n    kind: webkit\n    role: player\n    url: about:blank\n"
            "  - persona: monitor\n    kind: webkit\n    role: monitor\n    url: about:blank\n"
        ),
        encoding="utf-8",
    )
    macro_path.write_text('{"name":"alpha","actions":[]}', encoding="utf-8")
    return DemoBundle(
        id="role-based-duo",
        title="Role Based Duo",
        scenarios=["examples/scenarios/duo.yaml"],
        macro_refs=["examples/macros/alpha.json"],
        seed_refs=["seed/player.html", "seed/monitor.html"],
        replay_artifacts=["artifacts/replay.jsonl"],
        video_artifacts=["artifacts/demo.mp4", "artifacts/poster.png"],
        recording=DemoRecordingConfig(
            primary_role="player",
            role_seeds={"player": "seed/player.html", "monitor": "seed/monitor.html"},
            macros=[DemoMacroRun(name="alpha", role="player")],
        ),
        root=bundle_root,
    )


@pytest.mark.asyncio
async def test_record_demo_bundle_writes_expected_artifacts(monkeypatch, tmp_path: Path) -> None:
    bundle = _write_bundle_layout(tmp_path)

    def _fake_prepare_scenario(bundle_obj, scenario):
        runnable = scenario
        runnable._log_dir = bundle_obj.root / "artifacts" / "raw"
        runnable._log_dir.mkdir(parents=True, exist_ok=True)
        return runnable

    created_exports: list[Path] = []

    monkeypatch.setattr("octowright.demos.runtime.BrowserPool", _FakePool)
    monkeypatch.setattr("octowright.demos.runtime.ScenarioPool", _FakeScenarioPool)
    monkeypatch.setattr("octowright.demos.runtime._repo_root", lambda _: tmp_path)
    monkeypatch.setattr("octowright.demos.runtime._prepare_scenario", _fake_prepare_scenario)
    monkeypatch.setattr(
        "octowright.demos.runtime.write_exports",
        lambda replay_path: (
            created_exports.extend([replay_path.with_suffix(".py"), replay_path.with_suffix(".ts")])
            or replay_path.with_suffix(".py").write_text("python", encoding="utf-8")
            or replay_path.with_suffix(".ts").write_text("ts", encoding="utf-8")
        ),
    )
    monkeypatch.setattr(
        "octowright.demos.runtime.render_bundle_video",
        lambda bundle_obj, live, close_results, *, video_path, poster_path: (
            video_path.write_bytes(b"video"),
            poster_path.write_bytes(b"poster"),
            {"mode": "single", "canvas_width": 800, "canvas_height": 600, "panes": [], "overlay": {"title": "A"}},
        )[-1],
    )
    monkeypatch.setattr(
        "octowright.demos.runtime.write_artifact_manifest",
        lambda *args, **kwargs: ((kwargs["video_path"].parent / "manifest.json").write_text("{}", encoding="utf-8")),
    )
    result = await record_demo_bundle(bundle)

    replay_path = bundle.root / "artifacts" / "replay.jsonl"
    roundtrip_path = bundle.root / "artifacts" / "replay-roundtrip.jsonl"
    video_path = bundle.root / "artifacts" / "demo.mp4"
    poster_path = bundle.root / "artifacts" / "poster.png"
    assert result["bundle_id"] == bundle.id
    assert replay_path.exists()
    assert roundtrip_path.exists()
    assert video_path.exists()
    assert poster_path.exists()
    assert created_exports == [replay_path.with_suffix(".py"), replay_path.with_suffix(".ts")]


def test_render_bundle_video_uses_presentation_mode_for_sync_multi(monkeypatch, tmp_path: Path) -> None:
    bundle = DemoBundle(id="alpha", title="Alpha", root=tmp_path)
    bundle.presentation = DemoPresentationConfig(mode="sync-multi")

    source_video = tmp_path / "alpha.webm"
    source_video.write_bytes(b"source")
    mirror_video = tmp_path / "mirror.webm"
    mirror_video.write_bytes(b"mirror")
    live = LiveScenario(
        scenario_id="live-alpha",
        name="alpha",
        spec=None,
        participants=[
            {
                "instance_id": "iid-1",
                "persona": "alpha",
                "role": "player",
                "kind": "chromium",
            },
            {
                "instance_id": "iid-2",
                "persona": "mirror",
                "role": "monitor",
                "kind": "firefox",
            },
        ],
    )
    close_results = {
        "iid-1": {"video_path": str(source_video)},
        "iid-2": {"video_path": str(mirror_video)},
    }
    video_path = tmp_path / "demo.mp4"
    poster_path = tmp_path / "poster.png"

    called: dict[str, object] = {}

    def _fake_render_sync_group_videos(*args, **kwargs):
        called["sync"] = True
        return [
            {
                "id": "player",
                "path": str(tmp_path / "supporting" / "player.mp4"),
                "poster_path": str(tmp_path / "supporting" / "player.png"),
            },
            {
                "id": "monitor",
                "path": str(tmp_path / "supporting" / "monitor.mp4"),
                "poster_path": str(tmp_path / "supporting" / "monitor.png"),
            },
        ]

    monkeypatch.setattr(
        "octowright.demos.rendering.render_sync_group_videos", _fake_render_sync_group_videos, raising=False
    )
    monkeypatch.setattr(
        "octowright.demos.rendering.transcode_video",
        lambda source, target: target.write_bytes(Path(source).read_bytes()) or target,
    )
    monkeypatch.setattr(
        "octowright.demos.rendering.extract_frame",
        lambda source, target: target.write_bytes(b"poster") or target,
    )
    monkeypatch.setattr(
        "octowright.demos.rendering.compose_video_grid",
        lambda sources, target, **kwargs: target.write_bytes(b"video") or target,
    )
    monkeypatch.setattr(
        "octowright.demos.rendering.probe_video",
        lambda _: {"width": 1280, "height": 720, "duration_seconds": 2.0},
    )

    summary = render_bundle_video(bundle, live, close_results, video_path=video_path, poster_path=poster_path)

    assert summary["mode"] == "sync-multi"
    assert called["sync"] is True
    assert summary["supporting_videos"] == [
        {
            "id": "player",
            "path": str(tmp_path / "supporting" / "player.mp4"),
            "poster_path": str(tmp_path / "supporting" / "player.png"),
        },
        {
            "id": "monitor",
            "path": str(tmp_path / "supporting" / "monitor.mp4"),
            "poster_path": str(tmp_path / "supporting" / "monitor.png"),
        },
    ]


def test_render_bundle_video_raises_when_sync_multi_plan_produces_no_panes(monkeypatch, tmp_path: Path) -> None:
    bundle = DemoBundle(id="alpha", title="Alpha", root=tmp_path)
    bundle.presentation = DemoPresentationConfig(mode="sync-multi")

    live = LiveScenario(
        scenario_id="live-alpha",
        name="alpha",
        spec=None,
        participants=[
            {
                "instance_id": "iid-1",
                "persona": "alpha",
                "role": "player",
                "kind": "chromium",
            },
            {
                "instance_id": "iid-2",
                "persona": "mirror",
                "role": "monitor",
                "kind": "firefox",
            },
        ],
    )

    monkeypatch.setattr(
        "octowright.demos.rendering.render_sync_group_videos",
        lambda *args, **kwargs: [],
    )

    with pytest.raises(RuntimeError, match="sync-multi"):
        render_bundle_video(
            bundle,
            live,
            {},
            video_path=tmp_path / "demo.mp4",
            poster_path=tmp_path / "poster.png",
        )


def test_select_render_plan_raises_for_unsupported_mode(tmp_path: Path) -> None:
    bundle = DemoBundle(id="alpha", title="Alpha", root=tmp_path)
    bundle.presentation.mode = "future-mode"

    with pytest.raises(ValueError, match="unsupported presentation mode"):
        select_render_plan(bundle)
