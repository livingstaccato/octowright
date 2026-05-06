# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from octowright.demos.models import DemoBundle


def _artifact_summary(bundle: DemoBundle, rel_paths: list[str]) -> dict[str, object]:
    existing = [path for path in rel_paths if (bundle.root / path).exists()]
    return {
        "declared_count": len(rel_paths),
        "existing_count": len(existing),
        "declared_paths": rel_paths,
        "existing_paths": existing,
    }


def _generation_status(*artifact_groups: dict[str, object]) -> str:
    declared_count = sum(int(group["declared_count"]) for group in artifact_groups)
    existing_count = sum(int(group["existing_count"]) for group in artifact_groups)
    if declared_count == 0:
        return "not-generated"
    if existing_count == 0:
        return "not-generated"
    if existing_count == declared_count:
        return "generated"
    return "partial"


def _last_generated(bundle: DemoBundle, *artifact_groups: dict[str, object]) -> str:
    existing_paths: list[Path] = []
    for group in artifact_groups:
        existing_paths.extend(bundle.root / path for path in group["existing_paths"])
    if not existing_paths:
        return "n/a"
    latest = max(path.stat().st_mtime for path in existing_paths)
    return datetime.fromtimestamp(latest, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def build_manifest_row(bundle: DemoBundle) -> dict[str, object]:
    replay = _artifact_summary(bundle, bundle.replay_artifacts)
    video = _artifact_summary(bundle, bundle.video_artifacts)
    return {
        "id": bundle.id,
        "title": bundle.title,
        "summary": bundle.summary,
        "hero": bundle.hero,
        "audiences": bundle.audiences,
        "tags": bundle.tags,
        "engines": bundle.engines,
        "roles": bundle.roles,
        "scenarios": bundle.scenarios,
        "regen_command": bundle.regen_command,
        "tutorial_export": bundle.tutorial_export,
        "artifacts": {
            "replay": replay,
            "video": video,
        },
    }


def _artifact_hint(replay: dict[str, object], video: dict[str, object]) -> str:
    return (
        f"Artifacts: replay {replay['existing_count']}/{replay['declared_count']}, "
        f"video {video['existing_count']}/{video['declared_count']}"
    )


def _artifact_paths_line(label: str, artifact_group: dict[str, object]) -> str:
    declared_paths = artifact_group["declared_paths"]
    existing_paths = artifact_group["existing_paths"]
    if not declared_paths:
        return f"{label}: none declared"
    parts: list[str] = []
    if existing_paths:
        parts.append("existing " + ", ".join(f"`{path}`" for path in existing_paths))
    parts.append("declared " + ", ".join(f"`{path}`" for path in declared_paths))
    return f"{label}: " + "; ".join(parts)


def _render_bundle_entry(bundle: DemoBundle) -> list[str]:
    replay = _artifact_summary(bundle, bundle.replay_artifacts)
    video = _artifact_summary(bundle, bundle.video_artifacts)
    lines = [
        f"### {bundle.title}",
        "",
        f"- ID: `{bundle.id}`",
        f"- Summary: {bundle.summary or 'No summary provided.'}",
        f"- Tags: {', '.join(f'`{tag}`' for tag in bundle.tags) or 'none'}",
        f"- Audiences: {', '.join(f'`{audience}`' for audience in bundle.audiences) or 'none'}",
        f"- Regen: `{bundle.regen_command or 'not set'}`",
        f"- {_artifact_hint(replay, video)}",
        f"- Generation status: {_generation_status(replay, video)}",
        f"- Last generated: {_last_generated(bundle, replay, video)}",
        f"- {_artifact_paths_line('Replay artifacts', replay)}",
        f"- {_artifact_paths_line('Video artifacts', video)}",
        "",
    ]
    return lines


def _render_section(lines: list[str], bundles: list[DemoBundle], empty_message: str) -> None:
    if not bundles:
        lines.extend([empty_message, ""])
        return
    for bundle in bundles:
        lines.extend(_render_bundle_entry(bundle))


def build_demo_index(bundles: list[DemoBundle]) -> str:
    heroes = [bundle for bundle in bundles if bundle.hero]
    supporting = [bundle for bundle in bundles if not bundle.hero]
    lines = [
        "# Octowright Demo Catalog",
        "",
        "## Hero Demos",
        "",
    ]
    _render_section(lines, heroes, "No hero demos yet.")
    lines.extend(
        [
            "",
            "## Full Library",
            "",
            "Supporting demos appear here for the complete non-hero catalog; hero demos are featured above.",
            "",
        ]
    )
    _render_section(lines, supporting, "No supporting demos yet.")
    return "\n".join(lines) + "\n"
