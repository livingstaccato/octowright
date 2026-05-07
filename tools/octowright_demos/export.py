# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from octowright_demos.models import DemoBundle


def _build_media_payload(manifest: dict[str, object]) -> dict[str, object]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
    presentation = manifest.get("presentation")
    if not isinstance(presentation, dict):
        presentation = {}
    primary = artifacts.get("video")
    if not isinstance(primary, dict):
        primary = {}
    supporting = artifacts.get("supporting_videos")
    if not isinstance(supporting, list):
        supporting = []
    return {
        "primary": primary,
        "supporting": supporting,
        "presentation": presentation,
    }


def build_tutorial_export(bundle: DemoBundle, *, tutorial_export_path: Path | str | None = None) -> dict[str, object]:
    resolved_export = bundle.tutorial_export
    if tutorial_export_path is not None:
        path_value = Path(tutorial_export_path)
        try:
            resolved_export = path_value.relative_to(bundle.root).as_posix()
        except ValueError:
            resolved_export = path_value.as_posix()
    payload = {
        "id": bundle.id,
        "title": bundle.title,
        "summary": bundle.summary,
        "hero": bundle.hero,
        "tutorial_export": resolved_export,
        "regen_command": bundle.regen_command,
        "assets": {
            "video": list(bundle.video_artifacts),
            "replay": list(bundle.replay_artifacts),
        },
    }
    manifest_path = bundle.root / "artifacts" / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["artifact_manifest"] = manifest
        if isinstance(manifest, dict):
            payload["media"] = _build_media_payload(manifest)
    return payload
