# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
    replay_artifacts: list[str] = field(default_factory=list)
    video_artifacts: list[str] = field(default_factory=list)
    regen_command: str | None = None
    tutorial_export: str | None = None
    root: Path = field(default_factory=Path)
