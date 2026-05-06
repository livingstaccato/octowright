# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from starlette.testclient import TestClient

from octowright import http as _http
from octowright.demos import catalog as _demo_catalog
from octowright.http.routes import demos as _demo_routes
from octowright.server import _state


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    fake_pool = SimpleNamespace(_sessions={})
    fake_spool = SimpleNamespace(list_live=lambda: [])
    monkeypatch.setattr(_state, "pool", fake_pool)
    monkeypatch.setattr(_state, "scenario_pool", fake_spool)
    return TestClient(_http.build_app())


def _write_bundle(root: Path, name: str, doc: dict[str, object]) -> Path:
    bundle_dir = root / name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "demo.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return bundle_dir


def test_demo_catalog_endpoint_groups_heroes_and_supporting(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hero_dir = _write_bundle(
        tmp_path,
        "alpha-hero",
        {
            "id": "alpha-hero",
            "title": "Alpha Hero",
            "summary": "Featured walkthrough.",
            "hero": True,
            "audiences": ["sales"],
            "tags": ["featured"],
            "engines": ["chromium"],
            "roles": ["host"],
            "source_refs": {"scenarios": ["alpha-demo"]},
            "artifact_expectations": {
                "replay": ["artifacts/replay.py"],
                "video": ["videos/highlight.mp4"],
            },
            "regen": {"command": "uv run octowright demo regen alpha-hero"},
            "tutorial_export": {"include": "exports/alpha.md"},
        },
    )
    (hero_dir / "artifacts").mkdir()
    (hero_dir / "videos").mkdir()
    (hero_dir / "artifacts" / "replay.py").write_text("# demo", encoding="utf-8")
    _write_bundle(
        tmp_path,
        "beta-support",
        {
            "id": "beta-support",
            "title": "Beta Support",
            "summary": "Library-only walkthrough.",
            "hero": False,
            "audiences": ["support"],
            "tags": ["library"],
            "engines": ["firefox"],
            "roles": ["observer"],
            "source_refs": {"scenarios": ["beta-demo"]},
            "artifact_expectations": {
                "replay": ["artifacts/replay.ts"],
                "video": [],
            },
        },
    )

    monkeypatch.setattr(_demo_catalog, "DEMO_BUNDLES_DIR", tmp_path)

    r = client.get("/api/demos")

    assert r.status_code == 200
    assert r.json() == {
        "heroes": [
            {
                "id": "alpha-hero",
                "title": "Alpha Hero",
                "summary": "Featured walkthrough.",
                "hero": True,
                "audiences": ["sales"],
                "tags": ["featured"],
                "engines": ["chromium"],
                "roles": ["host"],
                "scenarios": ["alpha-demo"],
                "regen_command": "uv run octowright demo regen alpha-hero",
                "tutorial_export": "exports/alpha.md",
                "artifacts": {
                    "replay": {
                        "declared_count": 1,
                        "existing_count": 1,
                        "declared_paths": ["artifacts/replay.py"],
                        "existing_paths": ["artifacts/replay.py"],
                    },
                    "video": {
                        "declared_count": 1,
                        "existing_count": 0,
                        "declared_paths": ["videos/highlight.mp4"],
                        "existing_paths": [],
                    },
                },
            }
        ],
        "supporting": [
            {
                "id": "beta-support",
                "title": "Beta Support",
                "summary": "Library-only walkthrough.",
                "hero": False,
                "audiences": ["support"],
                "tags": ["library"],
                "engines": ["firefox"],
                "roles": ["observer"],
                "scenarios": ["beta-demo"],
                "regen_command": None,
                "tutorial_export": None,
                "artifacts": {
                    "replay": {
                        "declared_count": 1,
                        "existing_count": 0,
                        "declared_paths": ["artifacts/replay.ts"],
                        "existing_paths": [],
                    },
                    "video": {
                        "declared_count": 0,
                        "existing_count": 0,
                        "declared_paths": [],
                        "existing_paths": [],
                    },
                },
            }
        ],
    }


def test_demo_detail_endpoint_404_when_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_demo_catalog, "DEMO_BUNDLES_DIR", tmp_path)

    r = client.get("/api/demos/missing-demo")

    assert r.status_code == 404
    assert r.json() == {"error": "demo 'missing-demo' not found"}


def test_demo_detail_endpoint_returns_manifest_row(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        "team-sync",
        {
            "id": "team-sync",
            "title": "Team Sync",
            "summary": "Multi-persona standup walkthrough.",
            "hero": True,
            "audiences": ["sales", "support"],
            "tags": ["collaboration"],
            "engines": ["chromium", "webkit"],
            "roles": ["host", "guest"],
            "source_refs": {"scenarios": ["standup"]},
            "artifact_expectations": {
                "replay": ["artifacts/replay.py"],
                "video": ["videos/intro.mp4"],
            },
            "regen": {"command": "uv run octowright demo regen team-sync"},
            "tutorial_export": {"include": "exports/team-sync.md"},
        },
    )
    (bundle_dir / "artifacts").mkdir()
    (bundle_dir / "videos").mkdir()
    (bundle_dir / "videos" / "intro.mp4").write_text("video", encoding="utf-8")

    monkeypatch.setattr(_demo_catalog, "DEMO_BUNDLES_DIR", tmp_path)

    r = client.get("/api/demos/team-sync")

    assert r.status_code == 200
    assert r.json() == {
        "id": "team-sync",
        "title": "Team Sync",
        "summary": "Multi-persona standup walkthrough.",
        "hero": True,
        "audiences": ["sales", "support"],
        "tags": ["collaboration"],
        "engines": ["chromium", "webkit"],
        "roles": ["host", "guest"],
        "scenarios": ["standup"],
        "regen_command": "uv run octowright demo regen team-sync",
        "tutorial_export": "exports/team-sync.md",
        "artifacts": {
            "replay": {
                "declared_count": 1,
                "existing_count": 0,
                "declared_paths": ["artifacts/replay.py"],
                "existing_paths": [],
            },
            "video": {
                "declared_count": 1,
                "existing_count": 1,
                "declared_paths": ["videos/intro.mp4"],
                "existing_paths": ["videos/intro.mp4"],
            },
        },
    }


def test_demo_catalog_endpoint_returns_structured_500_on_catalog_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> list[object]:
        raise ValueError("bad demo yaml")

    monkeypatch.setattr(_demo_routes, "list_demo_bundles", boom)

    r = client.get("/api/demos")

    assert r.status_code == 500
    assert r.json() == {"error": "demo catalog unavailable: bad demo yaml"}
