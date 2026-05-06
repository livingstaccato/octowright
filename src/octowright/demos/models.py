# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from octowright.demos.presentation import validate_presentation_mode


@dataclass
class DemoMacroRun:
    name: str
    role: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class DemoRecordingConfig:
    primary_role: str | None = None
    default_seed: str | None = None
    role_seeds: dict[str, str] = field(default_factory=dict)
    macros: list[DemoMacroRun] = field(default_factory=list)
    verify_report: str | None = None
    extras: list[str] = field(default_factory=list)


@dataclass
class DemoSyncGroup:
    id: str
    roles: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.roles:
            raise ValueError("presentation.sync_groups[*].roles must be a non-empty list[str]")


@dataclass
class DemoOverlayConfig:
    enabled: bool = True
    style: str = "subtle"
    placement: str = "bottom-left"


@dataclass
class DemoTimingConfig:
    intro_ms: int = 0
    outro_ms: int = 1500
    minimum_ms: int = 4000

    def __post_init__(self) -> None:
        for field_name in ("intro_ms", "outro_ms", "minimum_ms"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"presentation.timing.{field_name} must be >= 0")


@dataclass
class DemoPresentationConfig:
    mode: str = "single-clean"
    primary_asset: str = "hero_video"
    overlay: DemoOverlayConfig = field(default_factory=DemoOverlayConfig)
    timing: DemoTimingConfig = field(default_factory=DemoTimingConfig)
    sync_groups: list[DemoSyncGroup] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate_presentation_mode(self.mode)


@dataclass
class DemoBundle:
    id: str
    title: str
    summary: str | None = None
    hero: bool = False
    audiences: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)
    macro_refs: list[str] = field(default_factory=list)
    seed_refs: list[str] = field(default_factory=list)
    replay_artifacts: list[str] = field(default_factory=list)
    video_artifacts: list[str] = field(default_factory=list)
    regen_command: str | None = None
    tutorial_export: str | None = None
    recording: DemoRecordingConfig = field(default_factory=DemoRecordingConfig)
    presentation: DemoPresentationConfig = field(default_factory=DemoPresentationConfig)
    root: Path = field(default_factory=Path)

    @property
    def scenario_ref(self) -> str | None:
        return self.scenarios[0] if self.scenarios else None
