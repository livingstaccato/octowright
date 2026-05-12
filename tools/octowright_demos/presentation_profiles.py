# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass

from octowright_demos.models import DemoBundle


@dataclass(frozen=True)
class CompositePlacement:
    persona: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class RenderPlan:
    kind: str
    columns: int = 1
    cell_width: int = 0
    cell_height: int = 0
    canvas_width: int = 0
    canvas_height: int = 0
    placements: tuple[CompositePlacement, ...] = ()


_SYNC_MULTI_PRESETS: dict[str, RenderPlan] = {
    # 3 engines in a single row so all three cells are filled. The prior
    # 2-column layout left cell [1,1] empty, which ffmpeg's xstack rendered
    # as a saturated green panel in the published video.
    "cross-engine-trio": RenderPlan(kind="sync-multi", columns=3, cell_width=640, cell_height=360),
    # 2 browsers side-by-side at 16:9. The prior 960x1080 cells letterboxed
    # the 1280x720 sources with large black bars top and bottom.
    "role-based-duo": RenderPlan(kind="sync-multi", columns=2, cell_width=960, cell_height=540),
}

_HERO_COMPOSITE_PRESETS: dict[str, RenderPlan] = {
    # 3x3 evenly-tiled grid of all nine seven-mix participants. Every cell
    # shows a real browser; no slot dangles unmatched. 640x360 per cell at
    # 1920x1080 canvas.
    "seven-mix-orchestration": RenderPlan(
        kind="hero-composite",
        canvas_width=1920,
        canvas_height=1080,
        placements=(
            CompositePlacement("p1", 0, 0, 640, 360),
            CompositePlacement("p2", 640, 0, 640, 360),
            CompositePlacement("p3", 1280, 0, 640, 360),
            CompositePlacement("p4", 0, 360, 640, 360),
            CompositePlacement("p5", 640, 360, 640, 360),
            CompositePlacement("p6", 1280, 360, 640, 360),
            CompositePlacement("p7", 0, 720, 640, 360),
            CompositePlacement("ops", 640, 720, 640, 360),
            CompositePlacement("spectator", 1280, 720, 640, 360),
        ),
    )
}


def select_render_plan(bundle: DemoBundle) -> RenderPlan:
    if bundle.presentation.mode == "single-clean":
        return RenderPlan(kind="single-clean")
    if bundle.presentation.mode == "hero-composite":
        return resolve_composite_plan(bundle)
    if bundle.presentation.mode == "sync-multi":
        return resolve_sync_multi_plan(bundle)
    if bundle.presentation.mode == "artifact-first":
        return RenderPlan(kind="artifact-first")
    raise ValueError(f"unsupported presentation mode {bundle.presentation.mode!r}")


def resolve_sync_multi_plan(bundle: DemoBundle) -> RenderPlan:
    return _SYNC_MULTI_PRESETS.get(
        bundle.id,
        RenderPlan(kind="sync-multi", columns=2, cell_width=960, cell_height=540),
    )


def resolve_composite_plan(bundle: DemoBundle) -> RenderPlan:
    plan = _HERO_COMPOSITE_PRESETS.get(bundle.id)
    if plan is None:
        raise ValueError(f"no hero composite layout configured for {bundle.id!r}")
    return plan
