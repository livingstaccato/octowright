# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from octowright.demos.models import DemoBundle

DEMO_BUNDLES_DIR = Path("demo/bundles")


def _require_string_list(name: str, raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{name} must be a list[str]")
    if not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{name} must be a list[str]")
    return list(raw)


def _as_dict(name: str, raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    raise ValueError(f"{name} must be a mapping")


def _optional_string(name: str, raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    raise ValueError(f"{name} must be a string")


def _parse_hero(raw: Any) -> bool:
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise ValueError("hero must be a boolean or 'true'/'false' string")


def load_demo_bundle(bundle_dir: Path) -> DemoBundle:
    raw = yaml.safe_load((bundle_dir / "demo.yaml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raw = {}
    source_refs = _as_dict("source_refs", raw.get("source_refs"))
    artifact_expectations = _as_dict("artifact_expectations", raw.get("artifact_expectations"))
    regen = _as_dict("regen", raw.get("regen"))
    tutorial_export = _as_dict("tutorial_export", raw.get("tutorial_export"))
    return DemoBundle(
        id=_optional_string("id", raw.get("id")) or bundle_dir.name,
        title=_optional_string("title", raw.get("title")) or bundle_dir.name,
        summary=_optional_string("summary", raw.get("summary")),
        hero=_parse_hero(raw.get("hero")),
        audiences=_require_string_list("audiences", raw.get("audiences")),
        tags=_require_string_list("tags", raw.get("tags")),
        engines=_require_string_list("engines", raw.get("engines")),
        roles=_require_string_list("roles", raw.get("roles")),
        scenarios=_require_string_list("source_refs.scenarios", source_refs.get("scenarios")),
        replay_artifacts=_require_string_list("artifact_expectations.replay", artifact_expectations.get("replay")),
        video_artifacts=_require_string_list("artifact_expectations.video", artifact_expectations.get("video")),
        regen_command=_optional_string("regen.command", regen.get("command")),
        tutorial_export=_optional_string("tutorial_export.include", tutorial_export.get("include")),
        root=bundle_dir,
    )


def list_demo_bundles() -> list[DemoBundle]:
    if not DEMO_BUNDLES_DIR.exists():
        return []
    bundles = [
        load_demo_bundle(entry)
        for entry in DEMO_BUNDLES_DIR.iterdir()
        if entry.is_dir() and (entry / "demo.yaml").exists()
    ]
    bundles.sort(key=lambda bundle: (not bundle.hero, bundle.title))
    return bundles
