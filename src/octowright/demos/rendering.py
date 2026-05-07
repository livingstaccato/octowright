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
from octowright.demos.presentation_profiles import RenderPlan, select_render_plan
from octowright.export import export_script
from octowright.scenarios_pool import LiveScenario
from octowright.video import (
    apply_video_overlay,
    compose_video_grid,
    compose_video_layout,
    extract_frame,
    optimize_png,
    poster_capture_time,
    probe_video,
    render_supporting_video,
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
    if live is None:
        raise RuntimeError("cannot render demo video without a live scenario")
    plan = select_render_plan(bundle)
    work_path = _temporary_video_path(video_path)
    if plan.kind in {"single-clean", "artifact-first"}:
        primary = _primary_participant(live, bundle)
        source_video = _find_primary_video(live, close_results, bundle)
        transcode_video(source_video, work_path)
        summary = {
            "mode": plan.kind,
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

    if plan.kind == "sync-multi":
        try:
            panes = _grid_panes(
                live,
                close_results,
                columns=plan.columns,
                cell_width=plan.cell_width,
                cell_height=plan.cell_height,
            )
        except RuntimeError as exc:
            raise RuntimeError(f"sync-multi render plan for {bundle.id!r} did not produce any panes") from exc
        supporting_videos = render_sync_group_videos(panes, output_dir=video_path.parent / "supporting")
        source_videos = [pane["source"] for pane in panes]
        compose_video_grid(
            source_videos,
            work_path,
            columns=plan.columns,
            cell_width=plan.cell_width,
            cell_height=plan.cell_height,
        )
        summary = {
            "mode": "sync-multi",
            "canvas_width": plan.columns * plan.cell_width,
            "canvas_height": ((len(panes) - 1) // plan.columns + 1) * plan.cell_height,
            "columns": plan.columns,
            "cell_width": plan.cell_width,
            "cell_height": plan.cell_height,
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
            "supporting_videos": supporting_videos,
        }
    else:
        placements = _featured_video_placements(live, close_results, plan)
        supporting_videos = render_sync_group_videos(placements, output_dir=video_path.parent / "supporting")
        compose_video_layout(placements, work_path)
        summary = {
            "mode": "hero-composite",
            "canvas_width": plan.canvas_width,
            "canvas_height": plan.canvas_height,
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
            "supporting_videos": supporting_videos,
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
    supporting_videos = render_summary.get("supporting_videos", [])
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
        "presentation": {
            "mode": bundle.presentation.mode,
            "primary_asset": bundle.presentation.primary_asset,
        },
    }
    payload["artifacts"]["supporting_videos"] = [
        {
            "id": item["id"],
            "path": _relative_manifest_path(bundle, item["path"]),
            "poster_path": _relative_manifest_path(bundle, item["poster_path"]),
            "role": item["role"],
            "kind": item["kind"],
        }
        for item in supporting_videos
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("path"), str)
        and isinstance(item.get("poster_path"), str)
        and isinstance(item.get("role"), str)
        and isinstance(item.get("kind"), str)
    ]
    manifest_path = bundle.root / "artifacts" / "manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _temporary_video_path(video_path: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="octowright-demo-render-"))
    return temp_dir / video_path.name


def _relative_manifest_path(bundle: DemoBundle, raw_path: str) -> str:
    path = Path(raw_path)
    try:
        return path.relative_to(bundle.root).as_posix()
    except ValueError:
        return path.as_posix()


def _finalize_render(bundle: DemoBundle, *, work_path: Path, video_path: Path, summary: dict[str, Any]) -> None:
    work_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if bundle.hero and bundle.presentation.overlay.enabled:
            overlay = _overlay_payload(summary)
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
    extract_frame(video_path, poster_path, at_time=poster_capture_time(video_path))
    if poster_path.stat().st_size > 500_000:
        optimize_png(poster_path)


def _overlay_payload(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "",
        "subtitle": "",
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
            for pane in summary["panes"]
        ],
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


def render_sync_group_videos(
    sync_group_panes: list[dict[str, Any]],
    *,
    output_dir: Path,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, pane in enumerate(sync_group_panes, start=1):
        pane_id = _supporting_video_id(pane, index=index, used_ids=used_ids)
        output_path = output_dir / f"{pane_id}.mp4"
        poster_path = output_dir / f"{pane_id}.png"
        asset = render_supporting_video(Path(pane["source"]), output_path, poster_path=poster_path)
        rendered.append(
            {
                "id": pane_id,
                "path": asset["path"],
                "poster_path": asset["poster_path"],
                "persona": pane["persona"],
                "role": pane["role"],
                "kind": pane["kind"],
            }
        )
    return rendered


def _featured_video_placements(
    live: LiveScenario,
    close_results: dict[str, dict[str, Any]],
    plan: RenderPlan,
) -> list[dict[str, Any]]:
    by_persona = {participant["persona"]: participant for participant in live.participants}
    placements: list[dict[str, Any]] = []
    for slot in plan.placements:
        participant = by_persona.get(slot.persona)
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
                "x": slot.x,
                "y": slot.y,
                "width": slot.width,
                "height": slot.height,
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


def _supporting_video_id(pane: dict[str, Any], *, index: int, used_ids: set[str]) -> str:
    raw = str(pane.get("persona") or pane.get("role") or f"pane-{index}")
    base = "".join(char.lower() if char.isalnum() else "-" for char in raw).strip("-") or f"pane-{index}"
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


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
