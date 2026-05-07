# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass

from octowright.demos.models import DemoBundle


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
    "cross-engine-trio": RenderPlan(kind="sync-multi", columns=3, cell_width=640, cell_height=400),
    "role-based-duo": RenderPlan(kind="sync-multi", columns=2, cell_width=960, cell_height=720),
}

_HERO_COMPOSITE_PRESETS: dict[str, RenderPlan] = {
    "seven-mix-orchestration": RenderPlan(
        kind="hero-composite",
        canvas_width=1920,
        canvas_height=1080,
        placements=(
            CompositePlacement("p1", 0, 0, 1280, 720),
            CompositePlacement("ops", 1280, 0, 640, 360),
            CompositePlacement("spectator", 1280, 360, 640, 360),
            CompositePlacement("p2", 0, 720, 640, 360),
            CompositePlacement("p3", 640, 720, 640, 360),
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
