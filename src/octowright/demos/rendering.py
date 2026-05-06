# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import contextlib
import json
import tempfile
from pathlib import Path
from typing import Any

from octowright.demos.models import DemoBundle
from octowright.export import export_script
from octowright.scenarios_pool import LiveScenario
from octowright.video import (
    apply_video_overlay,
    compose_video_grid,
    compose_video_layout,
    extract_frame,
    optimize_png,
    probe_video,
    transcode_video,
)


def write_exports(replay_path: Path) -> None:
    python_export = replay_path.with_suffix(".py")
    export_script(replay_path, python_export, fmt="python")
    _ensure_spdx_header(python_export)
    export_script(replay_path, replay_path.with_suffix(".ts"), fmt="ts")


def render_bundle_video(
    bundle: DemoBundle,
    live: LiveScenario | None,
    close_results: dict[str, dict[str, Any]],
    *,
    video_path: Path,
    poster_path: Path,
) -> dict[str, Any]:
    profile = _composition_profile(bundle.id)
    if live is None:
        raise RuntimeError("cannot render demo video without a live scenario")
    work_path = _temporary_video_path(video_path)
    if profile is None:
        primary = _primary_participant(live, bundle)
        source_video = _find_primary_video(live, close_results, bundle)
        transcode_video(source_video, work_path)
        summary = {
            "mode": "single",
            "canvas_width": 0,
            "canvas_height": 0,
            "panes": [],
            "primary": {
                "persona": primary["persona"],
                "role": primary["role"],
                "kind": primary["kind"],
            },
        }
        summary["panes"] = [
            {
                "persona": primary["persona"],
                "role": primary["role"],
                "kind": primary["kind"],
                "x": 0,
                "y": 0,
                "width": 0,
                "height": 0,
            }
        ]
        metadata = probe_video(work_path)
        summary["canvas_width"] = int(metadata["width"])
        summary["canvas_height"] = int(metadata["height"])
        _finalize_render(bundle, work_path=work_path, video_path=video_path, summary=summary)
        _finalize_poster(video_path, poster_path)
        return summary

    if profile["mode"] == "grid":
        panes = _grid_panes(
            live,
            close_results,
            columns=int(profile["columns"]),
            cell_width=int(profile["cell_width"]),
            cell_height=int(profile["cell_height"]),
        )
        source_videos = [pane["source"] for pane in panes]
        compose_video_grid(
            source_videos,
            work_path,
            columns=int(profile["columns"]),
            cell_width=int(profile["cell_width"]),
            cell_height=int(profile["cell_height"]),
        )
        summary = {
            "mode": "grid",
            "canvas_width": int(profile["columns"]) * int(profile["cell_width"]),
            "canvas_height": ((len(panes) - 1) // int(profile["columns"]) + 1) * int(profile["cell_height"]),
            "columns": int(profile["columns"]),
            "cell_width": int(profile["cell_width"]),
            "cell_height": int(profile["cell_height"]),
            "panes": [
                {
                    "persona": pane["persona"],
                    "role": pane["role"],
                    "kind": pane["kind"],
                    "x": pane["x"],
                    "y": pane["y"],
                    "width": pane["width"],
                    "height": pane["height"],
                }
                for pane in panes
            ],
        }
    else:
        placements = _featured_video_placements(live, close_results, bundle.id)
        compose_video_layout(placements, work_path)
        summary = {
            "mode": "featured",
            "canvas_width": 1920,
            "canvas_height": 1080,
            "panes": [
                {
                    "persona": item["persona"],
                    "role": item["role"],
                    "kind": item["kind"],
                    "x": item["x"],
                    "y": item["y"],
                    "width": item["width"],
                    "height": item["height"],
                }
                for item in placements
            ],
        }
    _finalize_render(bundle, work_path=work_path, video_path=video_path, summary=summary)
    _finalize_poster(video_path, poster_path)
    return summary


def write_artifact_manifest(
    bundle: DemoBundle,
    live: LiveScenario | None,
    *,
    replay_path: Path,
    video_path: Path,
    poster_path: Path,
    event_count: int,
    render_summary: dict[str, Any],
) -> None:
    payload: dict[str, Any] = {
        "bundle_id": bundle.id,
        "title": bundle.title,
        "hero": bundle.hero,
        "participants": [
            {
                "persona": participant["persona"],
                "role": participant["role"],
                "kind": participant["kind"],
            }
            for participant in (live.participants if live is not None else [])
        ],
        "artifacts": {
            "replay": {
                "path": replay_path.relative_to(bundle.root).as_posix(),
                "event_count": event_count,
                "size_bytes": replay_path.stat().st_size,
                "python_export": replay_path.with_suffix(".py").relative_to(bundle.root).as_posix(),
                "ts_export": replay_path.with_suffix(".ts").relative_to(bundle.root).as_posix(),
            },
            "video": {
                "path": video_path.relative_to(bundle.root).as_posix(),
                "poster_path": poster_path.relative_to(bundle.root).as_posix(),
                "size_bytes": video_path.stat().st_size,
                **probe_video(video_path),
            },
        },
        "composition": render_summary,
    }
    manifest_path = bundle.root / "artifacts" / "manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _temporary_video_path(video_path: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="octowright-demo-render-"))
    return temp_dir / video_path.name


def _finalize_render(bundle: DemoBundle, *, work_path: Path, video_path: Path, summary: dict[str, Any]) -> None:
    work_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if bundle.hero:
            overlay = _overlay_payload(bundle, summary)
            apply_video_overlay(
                work_path,
                video_path,
                title=overlay["title"],
                subtitle=overlay["subtitle"],
                panes=overlay["panes"],
                canvas_width=int(summary["canvas_width"]),
                canvas_height=int(summary["canvas_height"]),
            )
            summary["overlay"] = overlay
        else:
            video_path.parent.mkdir(parents=True, exist_ok=True)
            work_path.replace(video_path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            work_path.unlink()
        with contextlib.suppress(OSError):
            work_path.parent.rmdir()


def _finalize_poster(video_path: Path, poster_path: Path) -> None:
    extract_frame(video_path, poster_path)
    if poster_path.stat().st_size > 500_000:
        optimize_png(poster_path)


def _overlay_payload(bundle: DemoBundle, summary: dict[str, Any]) -> dict[str, Any]:
    panes = [
        {
            "persona": pane["persona"],
            "role": pane["role"],
            "kind": pane["kind"],
            "x": pane["x"],
            "y": pane["y"],
        }
        for pane in summary["panes"]
    ]
    subtitle_parts = [bundle.id]
    if bundle.summary:
        subtitle_parts.append(bundle.summary)
    return {
        "title": bundle.title,
        "subtitle": " | ".join(subtitle_parts),
        "panes": panes,
    }


def _find_primary_video(
    live: LiveScenario,
    close_results: dict[str, dict[str, Any]],
    bundle: DemoBundle,
) -> Path:
    primary = _primary_participant(live, bundle)
    result = close_results.get(primary["instance_id"], {})
    raw = result.get("video_path")
    if not isinstance(raw, str) or not raw:
        raise RuntimeError(f"demo bundle {bundle.id!r} did not produce a source video")
    return Path(raw)


def _grid_panes(
    live: LiveScenario,
    close_results: dict[str, dict[str, Any]],
    *,
    columns: int,
    cell_width: int,
    cell_height: int,
) -> list[dict[str, Any]]:
    panes: list[dict[str, Any]] = []
    for index, participant in enumerate(live.participants):
        result = close_results.get(participant["instance_id"], {})
        raw_path = result.get("video_path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        panes.append(
            {
                "source": Path(raw_path),
                "persona": participant["persona"],
                "role": participant["role"],
                "kind": participant["kind"],
                "x": (index % columns) * cell_width,
                "y": (index // columns) * cell_height,
                "width": cell_width,
                "height": cell_height,
            }
        )
    if not panes:
        raise RuntimeError("no recorded participant videos were available for grid composition")
    return panes


def _composition_profile(bundle_id: str) -> dict[str, int | str] | None:
    profiles: dict[str, dict[str, int | str]] = {
        "cross-engine-trio": {"mode": "grid", "columns": 3, "cell_width": 640, "cell_height": 360},
        "role-based-duo": {"mode": "grid", "columns": 2, "cell_width": 960, "cell_height": 540},
        "seven-mix-orchestration": {"mode": "featured"},
    }
    return profiles.get(bundle_id)


def _featured_video_placements(
    live: LiveScenario,
    close_results: dict[str, dict[str, Any]],
    bundle_id: str,
) -> list[dict[str, Any]]:
    if bundle_id != "seven-mix-orchestration":
        raise ValueError(f"no featured layout configured for {bundle_id!r}")
    order = [
        ("p1", 0, 0, 1280, 720),
        ("p2", 1280, 0, 320, 360),
        ("p3", 1600, 0, 320, 360),
        ("p4", 1280, 360, 320, 360),
        ("p5", 1600, 360, 320, 360),
        ("p6", 0, 720, 480, 360),
        ("p7", 480, 720, 480, 360),
        ("ops", 960, 720, 480, 360),
        ("spectator", 1440, 720, 480, 360),
    ]
    by_persona = {participant["persona"]: participant for participant in live.participants}
    placements: list[dict[str, Any]] = []
    for persona, x, y, width, height in order:
        participant = by_persona.get(persona)
        if participant is None:
            continue
        result = close_results.get(participant["instance_id"], {})
        raw_path = result.get("video_path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        placements.append(
            {
                "source": Path(raw_path),
                "persona": participant["persona"],
                "role": participant["role"],
                "kind": participant["kind"],
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
        )
    if not placements:
        raise RuntimeError("no participant videos were available for featured composition")
    return placements


def _primary_participant(live: LiveScenario, bundle: DemoBundle) -> dict[str, Any]:
    primary_role = bundle.recording.primary_role
    if primary_role is None:
        return live.participants[0]
    for participant in live.participants:
        if participant["role"] == primary_role:
            return participant
    raise RuntimeError(f"demo bundle {bundle.id!r} primary role {primary_role!r} was not launched")


def _ensure_spdx_header(path: Path) -> None:
    header = (
        "# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc\n"
        "# SPDX-License-Identifier: Apache-2.0\n"
        "# SPDX-Comment: Part of octowright.\n"
        "#\n\n"
    )
    content = path.read_text(encoding="utf-8")
    if content.startswith("# SPDX-FileCopyrightText:"):
        return
    path.write_text(header + content, encoding="utf-8")
