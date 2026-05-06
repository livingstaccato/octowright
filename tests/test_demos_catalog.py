# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from octowright.demos.catalog import list_demo_bundles, load_demo_bundle


def _write_bundle(root: Path, name: str, doc: dict) -> Path:
    bundle_dir = root / name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "demo.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return bundle_dir


def test_load_demo_bundle_reads_demo_yaml_fields(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        "team-sync",
        {
            "id": "team-sync",
            "title": "Team Sync",
            "summary": "Multi-persona standup walkthrough.",
            "hero": True,
            "audiences": ["sales", "support"],
            "tags": ["collaboration", "video"],
            "engines": ["chromium", "webkit"],
            "roles": ["host", "guest"],
            "source_refs": {
                "scenarios": ["standup", "handoff"],
                "macros": ["macros/login.json"],
            },
            "artifact_expectations": {
                "replay": ["artifacts/replay.py", "artifacts/replay.ts"],
                "video": ["videos/intro.mp4", "videos/highlight.mp4"],
            },
            "regen": {
                "command": "uv run octowright demo regen team-sync",
            },
            "tutorial_export": {
                "include": "exports/team-sync.md",
            },
            "seed_refs": ["seed/stage.html"],
            "recording": {
                "primary_role": "host",
                "default_seed": "seed/stage.html",
                "role_seeds": {"guest": "seed/guest.html"},
                "macros": [
                    {"name": "login", "role": "host", "args": {"email": "demo@example.com"}},
                ],
                "verify_report": "artifacts/report.xml",
                "extras": ["participant-roster"],
            },
        },
    )

    bundle = load_demo_bundle(bundle_dir)

    assert bundle.id == "team-sync"
    assert bundle.title == "Team Sync"
    assert bundle.summary == "Multi-persona standup walkthrough."
    assert bundle.hero is True
    assert bundle.audiences == ["sales", "support"]
    assert bundle.tags == ["collaboration", "video"]
    assert bundle.engines == ["chromium", "webkit"]
    assert bundle.roles == ["host", "guest"]
    assert bundle.scenarios == ["standup", "handoff"]
    assert bundle.macro_refs == ["macros/login.json"]
    assert bundle.seed_refs == ["seed/stage.html"]
    assert bundle.replay_artifacts == ["artifacts/replay.py", "artifacts/replay.ts"]
    assert bundle.video_artifacts == ["videos/intro.mp4", "videos/highlight.mp4"]
    assert bundle.regen_command == "uv run octowright demo regen team-sync"
    assert bundle.tutorial_export == "exports/team-sync.md"
    assert bundle.recording.primary_role == "host"
    assert bundle.recording.default_seed == "seed/stage.html"
    assert bundle.recording.role_seeds == {"guest": "seed/guest.html"}
    assert len(bundle.recording.macros) == 1
    assert bundle.recording.macros[0].name == "login"
    assert bundle.recording.macros[0].role == "host"
    assert bundle.recording.macros[0].args == {"email": "demo@example.com"}
    assert bundle.recording.verify_report == "artifacts/report.xml"
    assert bundle.recording.extras == ["participant-roster"]
    assert bundle.root == bundle_dir


def test_list_demo_bundles_orders_heroes_first_then_title(monkeypatch, tmp_path: Path) -> None:
    _write_bundle(
        tmp_path,
        "zebra",
        {
            "id": "zebra",
            "title": "Zebra Flow",
            "hero": False,
        },
    )
    _write_bundle(
        tmp_path,
        "alpha-hero",
        {
            "id": "alpha-hero",
            "title": "Alpha Hero",
            "hero": True,
        },
    )
    _write_bundle(
        tmp_path,
        "beta-hero",
        {
            "id": "beta-hero",
            "title": "Beta Hero",
            "hero": True,
        },
    )
    _write_bundle(
        tmp_path,
        "alpha-plain",
        {
            "id": "alpha-plain",
            "title": "Alpha Plain",
            "hero": False,
        },
    )

    monkeypatch.setattr("octowright.demos.catalog.DEMO_BUNDLES_DIR", tmp_path)

    bundles = list_demo_bundles()

    assert [bundle.title for bundle in bundles] == [
        "Alpha Hero",
        "Beta Hero",
        "Alpha Plain",
        "Zebra Flow",
    ]


@pytest.mark.parametrize(
    ("raw_hero", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
    ],
)
def test_load_demo_bundle_parses_hero_safely(tmp_path: Path, raw_hero: object, expected: bool) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        "hero-check",
        {
            "id": "hero-check",
            "title": "Hero Check",
            "hero": raw_hero,
        },
    )

    bundle = load_demo_bundle(bundle_dir)

    assert bundle.hero is expected


def test_load_demo_bundle_rejects_malformed_list_fields(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        "bad-lists",
        {
            "id": "bad-lists",
            "title": "Bad Lists",
            "audiences": "sales",
            "artifact_expectations": {
                "replay": ["ok.py", 7],
            },
        },
    )

    with pytest.raises(ValueError, match="audiences"):
        load_demo_bundle(bundle_dir)


def test_load_demo_bundle_rejects_malformed_scalar_fields(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        "bad-scalars",
        {
            "id": "bad-scalars",
            "title": "Bad Scalars",
            "hero": False,
            "regen": {
                "command": ["uv", "run"],
            },
        },
    )

    with pytest.raises(ValueError, match=r"regen\.command"):
        load_demo_bundle(bundle_dir)


def test_load_demo_bundle_rejects_bad_recording_shape(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        "bad-recording",
        {
            "id": "bad-recording",
            "title": "Bad Recording",
            "recording": {
                "role_seeds": ["seed/stage.html"],
            },
        },
    )

    with pytest.raises(ValueError, match=r"recording\.role_seeds"):
        load_demo_bundle(bundle_dir)


def test_load_demo_bundle_rejects_invalid_hero_string(tmp_path: Path) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        "bad-hero",
        {
            "id": "bad-hero",
            "title": "Bad Hero",
            "hero": "maybe",
        },
    )

    with pytest.raises(ValueError, match="hero"):
        load_demo_bundle(bundle_dir)


def test_hero_demo_manifests_exist() -> None:
    ids = [
        "first-run-session",
        "macro-replay-loop",
        "cross-engine-trio",
        "role-based-duo",
        "fixture-lab",
        "verify-suite",
        "seven-mix-orchestration",
    ]
    for demo_id in ids:
        bundle_dir = Path("demo/bundles") / demo_id
        manifest = bundle_dir / "demo.yaml"
        assert manifest.exists(), demo_id
        payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        assert payload["hero"] is True
        macro_refs = payload["source_refs"]["macros"]
        seed_refs = payload["seed_refs"]
        assert macro_refs, demo_id
        assert seed_refs, demo_id
        for rel_path in macro_refs:
            assert Path(rel_path).exists(), f"{demo_id}: missing macro ref {rel_path}"
        for rel_path in seed_refs:
            assert (bundle_dir / rel_path).exists(), f"{demo_id}: missing seed ref {rel_path}"

        bundle = load_demo_bundle(bundle_dir)

        assert bundle.id == demo_id
        assert bundle.hero is True
        assert bundle.scenarios
        assert bundle.macro_refs, demo_id
        assert bundle.seed_refs, demo_id
        assert bundle.recording.primary_role, demo_id
        if demo_id == "role-based-duo":
            assert bundle.recording.role_seeds, demo_id
        else:
            assert bundle.recording.default_seed, demo_id
