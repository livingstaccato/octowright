# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import re
from pathlib import Path

from octowright import defaults
from octowright._paths import reject_unsafe_path

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_RUN_RE = re.compile(r"^run_(\d{4})$")


def slug(value: str) -> str:
    cleaned = _SLUG_RE.sub("-", value.strip()).strip("-.")
    if not cleaned:
        raise ValueError(f"artifact name {value!r} produced an empty slug")
    return cleaned


class ArtifactStore:
    def __init__(self, recordings_dir: Path | None = None) -> None:
        self.recordings_dir = (recordings_dir or defaults.RECORDINGS_DIR).expanduser()
        self.root = self.recordings_dir / "artifacts"

    def _contained(self, path: Path, *, label: str) -> Path:
        return reject_unsafe_path(path, self.root, label=label)

    def macro_dir(self, name: str) -> Path:
        target = self.root / "macros" / slug(name)
        target = self._contained(target, label=f"macro artifact {name!r}")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def macro_manifest_path(self, name: str) -> Path:
        return self.macro_dir(name) / "artifact.json"

    def next_run_dir(self, artifact_dir: Path) -> Path:
        runs_dir = self._contained(artifact_dir / "runs", label="artifact runs dir")
        runs_dir.mkdir(parents=True, exist_ok=True)
        max_seen = 0
        for child in runs_dir.iterdir():
            if not child.is_dir():
                continue
            match = _RUN_RE.match(child.name)
            if match is not None:
                max_seen = max(max_seen, int(match.group(1)))
        run_dir = runs_dir / f"run_{max_seen + 1:04d}"
        run_dir.mkdir(parents=False, exist_ok=False)
        return run_dir

    def resolve_macro_export_path(self, name: str, out_path: str | None) -> Path:
        if out_path:
            target = Path(out_path).expanduser()
            if not target.is_absolute():
                target = self.root / target
            target = reject_unsafe_path(target, self.recordings_dir, label="macro export path")
            target.parent.mkdir(parents=True, exist_ok=True)
            return target
        macro_slug = slug(name)
        exports_dir = self.macro_dir(name) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        return exports_dir / f"{macro_slug}.py"
