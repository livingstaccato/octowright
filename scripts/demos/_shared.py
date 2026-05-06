# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

# ruff: noqa: E402
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from octowright.demos.catalog import list_demo_bundles
from octowright.demos.export import build_tutorial_export
from octowright.demos.indexer import build_demo_index
from octowright.demos.models import DemoBundle
from octowright.demos.runtime import record_demo_bundle

INDEX_PATH = REPO_ROOT / "demo" / "INDEX.md"
TUTORIAL_EXPORT_ROOT = REPO_ROOT / "demo" / "tutorial-export"


def bundle_map() -> dict[str, DemoBundle]:
    return {bundle.id: bundle for bundle in list_demo_bundles()}


def rewrite_index(bundles: list[DemoBundle]) -> Path:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(build_demo_index(bundles), encoding="utf-8")
    return INDEX_PATH


def resolve_bundle_or_raise(demo_id: str) -> DemoBundle:
    bundles = bundle_map()
    try:
        return bundles[demo_id]
    except KeyError as exc:
        available = ", ".join(sorted(bundles)) or "none"
        raise SystemExit(f"unknown demo bundle: {demo_id}. available bundles: {available}") from exc


def prepare_bundle(bundle: DemoBundle) -> dict[str, object]:
    return build_tutorial_export(bundle)


def prepare_many(bundles: list[DemoBundle]) -> list[dict[str, object]]:
    return [prepare_bundle(bundle) for bundle in bundles]


def tutorial_export_output_path(bundle: DemoBundle) -> Path | None:
    if not bundle.tutorial_export:
        return None
    raw_path = bundle.root / bundle.tutorial_export
    if raw_path.suffix.lower() == ".json":
        return raw_path
    return raw_path.with_suffix(".json")


def write_tutorial_export(bundle: DemoBundle) -> Path | None:
    export_path = tutorial_export_output_path(bundle)
    if export_path is None:
        return None
    export_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_tutorial_export(bundle, tutorial_export_path=export_path)
    export_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return export_path


def write_many_tutorial_exports(bundles: list[DemoBundle]) -> list[tuple[DemoBundle, Path | None]]:
    return [(bundle, write_tutorial_export(bundle)) for bundle in bundles]


def record_bundle(bundle: DemoBundle) -> dict[str, object]:
    import asyncio

    return asyncio.run(record_demo_bundle(bundle))


def _bundle_artifacts_dir(bundle: DemoBundle) -> Path:
    return bundle.root / "artifacts"


def _sync_bundle_artifacts(bundle: DemoBundle, *, destination_root: Path) -> Path:
    target_dir = destination_root / bundle.id
    shutil.rmtree(target_dir, ignore_errors=True)
    source_dir = _bundle_artifacts_dir(bundle)
    if source_dir.exists():
        shutil.copytree(source_dir, target_dir)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def _relative_to(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def sync_tutorial_exports(bundles: list[DemoBundle], *, heroes_only: bool = False) -> list[Path]:
    selected = [bundle for bundle in bundles if bundle.hero or not heroes_only]
    shutil.rmtree(TUTORIAL_EXPORT_ROOT, ignore_errors=True)
    heroes_dir = TUTORIAL_EXPORT_ROOT / "heroes"
    artifacts_dir = TUTORIAL_EXPORT_ROOT / "artifacts"
    heroes_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    payload_paths: list[Path] = []
    index_rows: list[dict[str, object]] = []
    for bundle in selected:
        payload_path = heroes_dir / f"{bundle.id}.json"
        mirrored_artifacts_dir = _sync_bundle_artifacts(bundle, destination_root=artifacts_dir)
        payload = build_tutorial_export(
            bundle,
            tutorial_export_path=_relative_to(payload_path, TUTORIAL_EXPORT_ROOT),
        )
        payload_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        payload_paths.append(payload_path)
        index_rows.append(
            {
                "id": bundle.id,
                "title": bundle.title,
                "summary": bundle.summary,
                "payload": _relative_to(payload_path, TUTORIAL_EXPORT_ROOT),
                "artifacts_dir": _relative_to(mirrored_artifacts_dir, TUTORIAL_EXPORT_ROOT),
            }
        )

    index_payload = {"heroes": index_rows}
    (TUTORIAL_EXPORT_ROOT / "index.json").write_text(
        json.dumps(index_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_payload = {"version": 1, "hero_count": len(index_rows), "heroes": index_rows}
    (TUTORIAL_EXPORT_ROOT / "manifest.json").write_text(
        json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload_paths
