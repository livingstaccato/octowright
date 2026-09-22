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


def _highlight_notes(value: Any) -> list[tuple[str, str]]:
    """Extract title/body pairs from a highlight JSON document."""
    entries = value if isinstance(value, list) else [value]
    notes: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, dict):
            notes.extend((str(entry.get(field, "")), field) for field in ("title", "body"))
        else:
            notes.append((str(entry), "body"))
    return notes


def validate_local_sources(repo_root: Path = REPO_ROOT) -> int:
    """Validate the local changelog and highlight files, returning their count."""
    paths = local_note_paths(repo_root)
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            document = json.loads(text)
            for note, field in _highlight_notes(document):
                validate_notes(note, source=f"{path} ({field})")
        else:
            validate_notes(text, source=str(path))
    return len(paths)


if __name__ == "__main__":
    validate_local_sources()
