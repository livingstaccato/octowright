# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the local release-note policy guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._script_module import load_script_module

release_notes = load_script_module("scripts/github_release_notes.py")


@pytest.mark.parametrize(
    "note, reference",
    [
        ("Issue #247: fix the login flow", "Issue #247"),
        ("Issue 247: fix the login flow", "Issue 247"),
        ("Fix the login flow (#247)", "(#247)"),
        ("#247: fix the login flow", "#247:"),
        ("See https://github.com/acme/project/issues/247", "/issues/247"),
        ("Fixed (#181, #182)", "#181"),
        ("See #247 for details", "#247"),
    ],
)
def test_validate_notes_rejects_issue_references(note: str, reference: str) -> None:
    with pytest.raises(ValueError, match=r"release\.md:1") as error:
        release_notes.validate_notes(note, source="release.md")

    message = str(error.value)
    assert reference in message


def test_validate_notes_reports_the_line_for_a_reference() -> None:
    with pytest.raises(ValueError, match=r"release\.md:2") as error:
        release_notes.validate_notes("### Fixed\nIssue #247", source="release.md")

    assert "Issue #247" in str(error.value)


def test_validate_notes_rejects_empty_notes() -> None:
    with pytest.raises(ValueError, match="empty"):
        release_notes.validate_notes("  \n\t", source="release.md")


@pytest.mark.parametrize(
    "note",
    [
        "### Fixed\nImproved retry handling.",
        "Added support for CVE-2026-62949 remediation.",
        "Version 0.25.0 improves startup diagnostics.",
    ],
)
def test_validate_notes_allows_self_contained_prose(note: str) -> None:
    release_notes.validate_notes(note, source="release.md")


def test_local_note_paths_includes_changelog_and_sorted_highlights(tmp_path: Path) -> None:
    highlights = tmp_path / "src/octowright/upgrade/highlights"
    highlights.mkdir(parents=True)
    (highlights / "0.10.0.json").write_text("{}", encoding="utf-8")
    (highlights / "0.9.0.json").write_text("{}", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("notes", encoding="utf-8")

    assert release_notes.local_note_paths(tmp_path) == [
        tmp_path / "CHANGELOG.md",
        highlights / "0.10.0.json",
        highlights / "0.9.0.json",
    ]


def test_validate_local_sources_rejects_issue_reference_in_highlight(tmp_path: Path) -> None:
    highlights = tmp_path / "src/octowright/upgrade/highlights"
    highlights.mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text("### Fixed\nReliable startup.", encoding="utf-8")
    (highlights / "0.25.0.json").write_text('[{"title": "Issue #247 remains", "body": "Details"}]', encoding="utf-8")

    with pytest.raises(ValueError, match=r"0\.25\.0\.json.*Issue #247"):
        release_notes.validate_local_sources(tmp_path)


def test_validate_local_sources_returns_number_of_clean_sources(tmp_path: Path) -> None:
    highlights = tmp_path / "src/octowright/upgrade/highlights"
    highlights.mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text("### Fixed\nReliable startup.", encoding="utf-8")
    (highlights / "0.25.0.json").write_text(
        '[{"title": "Reliable startup", "body": "Improved diagnostics."}]',
        encoding="utf-8",
    )

    assert release_notes.validate_local_sources(tmp_path) == 2


@pytest.mark.parametrize(
    ("document", "error_fragment"),
    [
        ([], "non-empty list"),
        (None, "non-empty list"),
        (["not an object"], "entry 1 must be an object"),
        ([{"title": None, "body": "Body"}], "entry 1 title must be a string"),
        ([{"title": "Title", "body": None}], "entry 1 body must be a string"),
        ([{"title": 123, "body": "Body"}], "entry 1 title must be a string"),
        ([{"title": "Title", "body": ["Body"]}], "entry 1 body must be a string"),
    ],
)
def test_validate_local_sources_rejects_invalid_highlight_schema(
    tmp_path: Path, document: object, error_fragment: str
) -> None:
    highlights = tmp_path / "src/octowright/upgrade/highlights"
    highlights.mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text("### Fixed\nReliable startup.", encoding="utf-8")
    path = highlights / "0.25.0.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=rf"0\.25\.0\.json.*{error_fragment}"):
        release_notes.validate_local_sources(tmp_path)


def test_validate_local_sources_rejects_empty_highlight_field(tmp_path: Path) -> None:
    highlights = tmp_path / "src/octowright/upgrade/highlights"
    highlights.mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text("### Fixed\nReliable startup.", encoding="utf-8")
    path = highlights / "0.25.0.json"
    path.write_text('[{"title": "", "body": "Details"}]', encoding="utf-8")

    with pytest.raises(ValueError, match=r"0\.25\.0\.json.*entry 1 title.*empty"):
        release_notes.validate_local_sources(tmp_path)


def test_validate_local_sources_accepts_multiple_production_highlights(tmp_path: Path) -> None:
    highlights = tmp_path / "src/octowright/upgrade/highlights"
    highlights.mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text("### Fixed\nReliable startup.", encoding="utf-8")
    path = highlights / "0.25.0.json"
    path.write_text(
        json.dumps(
            [
                {"title": "Reliable startup", "body": "Improved diagnostics."},
                {"title": "Safer retries", "body": "Retries now back off cleanly."},
            ]
        ),
        encoding="utf-8",
    )

    assert release_notes.validate_local_sources(tmp_path) == 2


def test_validate_local_sources_reports_issue_in_later_highlight_body(tmp_path: Path) -> None:
    highlights = tmp_path / "src/octowright/upgrade/highlights"
    highlights.mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text("### Fixed\nReliable startup.", encoding="utf-8")
    path = highlights / "0.25.0.json"
    path.write_text(
        '[{"title": "First", "body": "Details"}, {"title": "Second", "body": "See #247 for details"}]',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"0\.25\.0\.json.*entry 2 body.*#247"):
        release_notes.validate_local_sources(tmp_path)
