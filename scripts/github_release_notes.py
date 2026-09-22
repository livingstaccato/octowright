# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Validate release notes before they are copied into a GitHub release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPOSITORY = "livingstaccato/octowright"
_HIGHLIGHTS_DIR = Path("src/octowright/upgrade/highlights")

# These forms are issue-tracker references, not ordinary Markdown headings or
# identifiers such as CVE-2026-62949. Numeric Markdown headings remain valid;
# numeric ``#`` forms are intentionally not allowed anywhere in release notes.
_REFERENCE_PATTERNS = (
    re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[0-9]+\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+#[0-9]+\b"),
    re.compile(r"\bissues?\s+#?[0-9]+\b", re.IGNORECASE),
    re.compile(r"\bGH-[0-9]+\b", re.IGNORECASE),
    re.compile(r"\(#[0-9]+\)"),
    re.compile(r"^\s*#[0-9]+\s*:", re.MULTILINE),
    re.compile(r"/issues/[0-9]+\b"),
    re.compile(r"#[0-9]+\b"),
)
_TAG_RE = re.compile(
    r"\Av(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


class CommandRunner(Protocol):
    """The narrow subprocess interface this script needs."""

    def __call__(
        self,
        args: list[str],
        *,
        check: bool,
        text: bool,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]: ...


def validate_tag(tag: str) -> None:
    """Raise ``ValueError`` unless *tag* is a v-prefixed semantic version."""
    if not _TAG_RE.fullmatch(tag):
        raise ValueError(f"invalid release tag: {tag!r}")


def create_draft(
    tag: str,
    notes_file: Path,
    *,
    repository: str = DEFAULT_REPOSITORY,
    runner: CommandRunner = subprocess.run,
) -> None:
    """Create a GitHub draft after validating the tag and note body locally."""
    validate_tag(tag)
    resolved_notes_file = notes_file.resolve(strict=True)
    notes = resolved_notes_file.read_text(encoding="utf-8")
    validate_notes(notes, source=str(resolved_notes_file))
    command = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        repository,
        "--title",
        tag,
        "--notes-file",
        str(resolved_notes_file),
        "--draft",
        "--verify-tag",
    ]
    runner(command, check=True, text=True)  # nosec B603


def fetch_releases(
    repository: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> list[dict[str, object]]:
    """Fetch every release from GitHub, rejecting ambiguous API output."""
    command = [
        "gh",
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repository}/releases?per_page=100",
    ]
    result = runner(command, check=True, text=True, capture_output=True)  # nosec B603
    payload: object = json.loads(result.stdout)
    if not isinstance(payload, list):
        raise ValueError("GitHub releases response must be a list of pages")

    releases: list[dict[str, object]] = []
    tags: set[str] = set()
    for page_number, page in enumerate(payload, start=1):
        if not isinstance(page, list):
            raise ValueError(f"GitHub releases page {page_number} must be a list")
        for release_number, release in enumerate(page, start=1):
            if not isinstance(release, dict):
                raise ValueError(f"GitHub releases page {page_number} item {release_number} must be an object")
            record: dict[str, object] = {}
            for key, value in release.items():
                if not isinstance(key, str):
                    raise ValueError("GitHub release object keys must be strings")
                record[key] = value
            tag = record.get("tag_name")
            if not isinstance(tag, str):
                raise ValueError("GitHub release tag_name must be a string")
            if tag in tags:
                raise ValueError(f"GitHub releases response has duplicate tag {tag!r}")
            tags.add(tag)
            releases.append(record)
    return releases


def audit_remote(
    repository: str = DEFAULT_REPOSITORY,
    *,
    runner: CommandRunner = subprocess.run,
) -> int:
    """Validate release titles and bodies already published in *repository*."""
    releases = fetch_releases(repository, runner=runner)
    for release in releases:
        tag = release.get("tag_name")
        if not isinstance(tag, str):
            raise ValueError("GitHub release tag_name must be a string")
        validate_tag(tag)
        title = release.get("name")
        if title != tag:
            raise ValueError(f"release {tag}: title must exactly equal its tag")
        body = release.get("body")
        if not isinstance(body, str):
            raise ValueError(f"release {tag}: body must be a non-empty string")
        validate_notes(body, source=f"release {tag} body")
    return len(releases)


def _parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the release-note checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-local")
    check_body = commands.add_parser("check-body")
    check_body.add_argument("notes_file")
    create_draft_parser = commands.add_parser("create-draft")
    create_draft_parser.add_argument("tag")
    create_draft_parser.add_argument("notes_file")
    create_draft_parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    audit_remote_parser = commands.add_parser("audit-remote")
    audit_remote_parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a release-note policy command and return its process status."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "check-local":
            count = validate_local_sources()
            print(f"validated {count} local release-note sources")
        elif args.command == "check-body":
            notes_file = Path(args.notes_file)
            validate_notes(notes_file.read_text(encoding="utf-8"), source=str(notes_file))
            print("validated release note body")
        elif args.command == "create-draft":
            create_draft(args.tag, Path(args.notes_file), repository=args.repo)
            print(f"created draft {args.tag}")
        elif args.command == "audit-remote":
            count = audit_remote(args.repo)
            print(f"audited {count} remote releases")
        else:
            raise ValueError(f"unknown command: {args.command!r}")
    except subprocess.CalledProcessError as error:
        diagnostic = error.stderr
        if isinstance(diagnostic, bytes):
            diagnostic = diagnostic.decode("utf-8", errors="replace")
        print(diagnostic or str(error), file=sys.stderr)
        return 1
    except (json.JSONDecodeError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def validate_notes(text: str, *, source: str) -> None:
    """Raise ``ValueError`` when *text* is empty or names a specific issue."""
    if not text.strip():
        raise ValueError(f"{source}: empty release notes")

    matches = [match for pattern in _REFERENCE_PATTERNS if (match := pattern.search(text))]
    if matches:
        match = min(matches, key=lambda candidate: candidate.start())
        number = re.search(r"[0-9]+", match.group(0))
        number_offset = number.start() if number else 0
        line_number = text.count("\n", 0, match.start() + number_offset) + 1
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
    raise SystemExit(main())
