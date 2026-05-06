# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from octowright.demos.models import DemoBundle


def _load_script(monkeypatch, name: str):
    scripts_dir = Path("scripts/demos").resolve()
    monkeypatch.syspath_prepend(str(scripts_dir))
    sys.modules.pop("_shared", None)
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_record_demo_reports_unknown_demo_id(monkeypatch, capsys) -> None:
    record_demo = _load_script(monkeypatch, "record_demo")
    monkeypatch.setattr(record_demo, "bundle_map", lambda: {})

    exit_code = record_demo.main(["missing-demo"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "unknown demo bundle: missing-demo. available bundles: none\n"


def test_record_demo_rewrites_index_for_known_bundle(monkeypatch, tmp_path: Path, capsys) -> None:
    record_demo = _load_script(monkeypatch, "record_demo")
    shared = sys.modules["_shared"]
    bundle = DemoBundle(
        id="alpha-demo",
        title="Alpha Demo",
        summary="Primary walkthrough.",
        hero=True,
        regen_command="uv run python scripts/demos/record_demo.py alpha-demo",
        root=tmp_path / "bundle",
    )
    bundle.root.mkdir(parents=True)
    monkeypatch.setattr(record_demo, "bundle_map", lambda: {bundle.id: bundle})
    monkeypatch.setattr(
        record_demo,
        "record_bundle",
        lambda _: {"replay_path": "replay.jsonl", "video_path": "demo.mp4", "poster_path": "poster.png"},
    )
    monkeypatch.setattr(shared, "INDEX_PATH", tmp_path / "demo" / "INDEX.md")

    exit_code = record_demo.main([bundle.id])

    captured = capsys.readouterr()
    index_path = tmp_path / "demo" / "INDEX.md"
    assert exit_code == 0
    assert index_path.exists()
    assert "Alpha Demo" in index_path.read_text(encoding="utf-8")
    assert "recorded demo bundle: alpha-demo" in captured.out
    assert "tutorial export: not configured" in captured.out


def test_record_all_writes_tutorial_export_json(monkeypatch, tmp_path: Path, capsys) -> None:
    record_all = _load_script(monkeypatch, "record_all")
    shared = sys.modules["_shared"]
    bundle = DemoBundle(
        id="hero-demo",
        title="Hero Demo",
        summary="Primary walkthrough.",
        hero=True,
        replay_artifacts=["artifacts/replay.jsonl"],
        video_artifacts=["artifacts/demo.mp4"],
        regen_command="uv run python scripts/demos/record_demo.py hero-demo",
        tutorial_export="exports/hero-demo.json",
        root=tmp_path / "bundle",
    )
    bundle.root.mkdir(parents=True)
    monkeypatch.setattr(record_all, "list_demo_bundles", lambda: [bundle])
    monkeypatch.setattr(
        record_all, "record_bundle", lambda _: {"replay_path": "replay.jsonl", "video_path": "demo.mp4"}
    )
    monkeypatch.setattr(shared, "INDEX_PATH", tmp_path / "demo" / "INDEX.md")

    exit_code = record_all.main()

    captured = capsys.readouterr()
    export_path = bundle.root / "exports" / "hero-demo.json"
    assert exit_code == 0
    assert export_path.exists()
    assert json.loads(export_path.read_text(encoding="utf-8")) == {
        "id": "hero-demo",
        "title": "Hero Demo",
        "summary": "Primary walkthrough.",
        "hero": True,
        "tutorial_export": "exports/hero-demo.json",
        "regen_command": "uv run python scripts/demos/record_demo.py hero-demo",
        "assets": {
            "video": ["artifacts/demo.mp4"],
            "replay": ["artifacts/replay.jsonl"],
        },
    }
    assert "prepared demo bundles: 1" in captured.out
    assert "- hero-demo -> " in captured.out
    assert "replay=replay.jsonl video=demo.mp4" in captured.out


def test_record_demo_normalizes_non_json_tutorial_export_suffix(monkeypatch, tmp_path: Path, capsys) -> None:
    record_demo = _load_script(monkeypatch, "record_demo")
    shared = sys.modules["_shared"]
    bundle = DemoBundle(
        id="beta-demo",
        title="Beta Demo",
        summary="Secondary walkthrough.",
        hero=True,
        tutorial_export="exports/beta-demo.md",
        root=tmp_path / "bundle",
    )
    bundle.root.mkdir(parents=True)
    monkeypatch.setattr(record_demo, "bundle_map", lambda: {bundle.id: bundle})
    monkeypatch.setattr(
        record_demo,
        "record_bundle",
        lambda _: {"replay_path": "replay.jsonl", "video_path": "demo.mp4", "poster_path": "poster.png"},
    )
    monkeypatch.setattr(shared, "INDEX_PATH", tmp_path / "demo" / "INDEX.md")

    exit_code = record_demo.main([bundle.id])

    captured = capsys.readouterr()
    json_path = bundle.root / "exports" / "beta-demo.json"
    md_path = bundle.root / "exports" / "beta-demo.md"
    assert exit_code == 0
    assert json_path.exists()
    assert not md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["tutorial_export"] == "exports/beta-demo.json"
    assert f"tutorial export written: {json_path}" in captured.out


def test_record_heroes_writes_only_hero_exports(monkeypatch, tmp_path: Path, capsys) -> None:
    record_heroes = _load_script(monkeypatch, "record_heroes")
    shared = sys.modules["_shared"]
    hero = DemoBundle(
        id="hero-demo",
        title="Hero Demo",
        hero=True,
        tutorial_export="exports/hero-demo.md",
        root=tmp_path / "hero",
    )
    support = DemoBundle(
        id="support-demo",
        title="Support Demo",
        hero=False,
        tutorial_export="exports/support-demo.json",
        root=tmp_path / "support",
    )
    hero.root.mkdir(parents=True)
    support.root.mkdir(parents=True)
    monkeypatch.setattr(record_heroes, "list_demo_bundles", lambda: [hero, support])
    monkeypatch.setattr(
        record_heroes, "record_bundle", lambda _: {"replay_path": "replay.jsonl", "video_path": "demo.mp4"}
    )
    monkeypatch.setattr(shared, "INDEX_PATH", tmp_path / "demo" / "INDEX.md")

    exit_code = record_heroes.main()

    captured = capsys.readouterr()
    hero_json_path = hero.root / "exports" / "hero-demo.json"
    support_json_path = support.root / "exports" / "support-demo.json"
    assert exit_code == 0
    assert hero_json_path.exists()
    assert not (hero.root / "exports" / "hero-demo.md").exists()
    assert not support_json_path.exists()
    assert "prepared hero bundles: 1" in captured.out
    assert f"- hero-demo -> {hero_json_path}" in captured.out
    assert "replay=replay.jsonl video=demo.mp4" in captured.out
    assert "support-demo" not in captured.out
