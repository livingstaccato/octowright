# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Validate release notes before they are copied into a GitHub release."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
_HIGHLIGHTS_DIR = Path("src/octowright/upgrade/highlights")

# These forms are issue-tracker references, not ordinary Markdown headings or
# identifiers such as CVE-2026-62949. Keep the leading-number form anchored so
# a heading like ``### Fixed`` remains valid.
_REFERENCE_PATTERNS = (
    re.compile(r"\bIssue\s+#?\d+\b", re.IGNORECASE),
    re.compile(r"\(#\d+\)"),
    re.compile(r"^\s*#\d+\s*:"),
    re.compile(r"/issues/\d+\b"),
    re.compile(r"(?<![#\w-])#\d+\b"),
)


def validate_notes(text: str, *, source: str) -> None:
    """Raise ``ValueError`` when *text* is empty or names a specific issue."""
    if not text.strip():
        raise ValueError(f"{source}: empty release notes")

    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern in _REFERENCE_PATTERNS:
            match = pattern.search(line)
            if match:
                reference = match.group(0).strip()
                raise ValueError(f"{source}:{line_number}: issue reference {reference!r} is not allowed")


def local_note_paths(repo_root: Path) -> list[Path]:
    """Return the changelog followed by all versioned local highlights."""
    # Resolve the relative glob against the caller's root so temporary
    # repositories are supported while ordering remains deterministic.
    highlight_paths = sorted((repo_root / _HIGHLIGHTS_DIR).glob("*.json"))
    return [repo_root / "CHANGELOG.md", *highlight_paths]


def _validate_highlight_document(path: Path, value: Any) -> None:
    """Validate the production shape of one versioned highlight document."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path}: highlight entries must be a non-empty list")

    for entry_number, entry in enumerate(value, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: entry {entry_number} must be an object")
        for field in ("title", "body"):
            note = entry.get(field)
            if not isinstance(note, str):
                raise ValueError(f"{path}: entry {entry_number} {field} must be a string")
            validate_notes(note, source=f"{path}: entry {entry_number} {field}")


def validate_local_sources(repo_root: Path = REPO_ROOT) -> int:
    """Validate the local changelog and highlight files, returning their count."""
    paths = local_note_paths(repo_root)
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            _validate_highlight_document(path, json.loads(text))
        else:
            validate_notes(text, source=str(path))
    return len(paths)


if __name__ == "__main__":
    validate_local_sources()
