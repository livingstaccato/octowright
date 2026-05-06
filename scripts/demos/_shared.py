# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

# ruff: noqa: E402
import json
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

INDEX_PATH = REPO_ROOT / "demo" / "INDEX.md"


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
