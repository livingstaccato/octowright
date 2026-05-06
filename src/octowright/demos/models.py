# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    root: Path = field(default_factory=Path)

    @property
    def scenario_ref(self) -> str | None:
        return self.scenarios[0] if self.scenarios else None
