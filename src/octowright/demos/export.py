# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

from octowright.demos.models import DemoBundle


def build_tutorial_export(bundle: DemoBundle, *, tutorial_export_path: Path | str | None = None) -> dict[str, object]:
    resolved_export = bundle.tutorial_export
    if tutorial_export_path is not None:
        path_value = Path(tutorial_export_path)
        try:
            resolved_export = path_value.relative_to(bundle.root).as_posix()
        except ValueError:
            resolved_export = path_value.as_posix()
    return {
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
