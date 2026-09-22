# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the local release-note policy guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests._script_module import load_script_module

release_notes = load_script_module("scripts/github_release_notes.py")


class RecordingRunner:
    """Capture gh calls without starting a subprocess."""

    def __init__(self, stdout: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.stdout = stdout

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=self.stdout, stderr="")


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


@pytest.mark.parametrize(
    "tag",
    [
        "v0.26.0",
        "v1.2.3-rc.1",
        "v1.2.3-alpha.1+build.20260921",
        "v12.34.56+build.7",
    ],
)
def test_validate_tag_accepts_semver_release_tags(tag: str) -> None:
    release_notes.validate_tag(tag)


@pytest.mark.parametrize(
    "tag",
    ["0.26.0", "v0.26", "vnext", "v1.2.3\n--repo=attacker/repo", "v1.2.3 --draft"],
)
def test_validate_tag_rejects_non_semver_and_argument_like_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="release tag"):
        release_notes.validate_tag(tag)


@pytest.mark.parametrize(
    "tag",
    ["v1٢.2.3", "v\uff11.2.3", "v1.2.3-1٢", "v1.2.3-\uff112"],
)
def test_validate_tag_rejects_non_ascii_digits_in_core_and_prerelease(tag: str) -> None:
    with pytest.raises(ValueError, match="release tag"):
        release_notes.validate_tag(tag)


def test_create_draft_uses_a_fixed_safe_gh_command(tmp_path: Path) -> None:
    notes = tmp_path / "release.md"
    notes.write_text("### Fixed\nReliable startup.", encoding="utf-8")
    runner = RecordingRunner()

    release_notes.create_draft("v0.26.0", notes, runner=runner)

    assert runner.calls == [
        (
            [
                "gh",
                "release",
                "create",
                "v0.26.0",
                "--repo",
                "livingstaccato/octowright",
                "--title",
                "v0.26.0",
                "--notes-file",
                str(notes),
                "--draft",
                "--verify-tag",
            ],
            {"check": True, "text": True},
        )
    ]


@pytest.mark.parametrize(
    ("tag", "notes_text"),
    [("vnext", "### Fixed\nReliable startup."), ("v0.26.0", "See #247 for details")],
)
def test_create_draft_validates_before_calling_runner(tmp_path: Path, tag: str, notes_text: str) -> None:
    notes = tmp_path / "release.md"
    notes.write_text(notes_text, encoding="utf-8")
    runner = RecordingRunner()

    with pytest.raises(ValueError):
        release_notes.create_draft(tag, notes, runner=runner)

    assert runner.calls == []


def test_fetch_releases_flattens_paginated_gh_api_output() -> None:
    runner = RecordingRunner(
        json.dumps(
            [
                [{"tag_name": "v0.26.0", "name": "v0.26.0", "body": "First release."}],
                [{"tag_name": "v0.25.0", "name": "v0.25.0", "body": "Second release."}],
            ]
        )
    )

    assert release_notes.fetch_releases("livingstaccato/octowright", runner=runner) == [
        {"tag_name": "v0.26.0", "name": "v0.26.0", "body": "First release."},
        {"tag_name": "v0.25.0", "name": "v0.25.0", "body": "Second release."},
    ]
    assert runner.calls == [
        (
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                "repos/livingstaccato/octowright/releases?per_page=100",
            ],
            {"check": True, "text": True, "capture_output": True},
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        ["not a page"],
        [["not a release"]],
        [[{"tag_name": "v0.26.0"}], [{"tag_name": "v0.26.0"}]],
    ],
)
def test_fetch_releases_rejects_malformed_or_ambiguous_payloads(payload: object) -> None:
    runner = RecordingRunner(json.dumps(payload))

    with pytest.raises(ValueError):
        release_notes.fetch_releases("livingstaccato/octowright", runner=runner)


def test_audit_remote_accepts_clean_releases() -> None:
    runner = RecordingRunner(
        json.dumps(
            [
                [
                    {"tag_name": "v0.26.0", "name": "v0.26.0", "body": "Reliable startup."},
                    {"tag_name": "v0.25.0", "name": "v0.25.0", "body": "Safer retries."},
                ]
            ]
        )
    )

    assert release_notes.audit_remote(runner=runner) == 2


@pytest.mark.parametrize(
    "release",
    [
        {"tag_name": "v0.26.0", "name": "Release 0.26", "body": "Reliable startup."},
        {"tag_name": "v0.26.0", "name": "v0.26.0", "body": "  \n"},
        {"tag_name": "v0.26.0", "name": "v0.26.0", "body": None},
        {"tag_name": "vnext", "name": "vnext", "body": "Reliable startup."},
        {"tag_name": "v0.26.0", "name": "v0.26.0", "body": "See #247 for details."},
    ],
)
def test_audit_remote_rejects_invalid_release_metadata(release: dict[str, object]) -> None:
    runner = RecordingRunner(json.dumps([[release]]))

    with pytest.raises(ValueError):
        release_notes.audit_remote(runner=runner)


def test_main_check_body_reports_success_and_validation_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = tmp_path / "release.md"
    notes.write_text("Reliable startup.", encoding="utf-8")

    assert release_notes.main(["check-body", str(notes)]) == 0
    assert "validated release note body" in capsys.readouterr().out

    notes.write_text("See #247 for details.", encoding="utf-8")
    assert release_notes.main(["check-body", str(notes)]) == 1
    assert "issue reference" in capsys.readouterr().err


def test_main_uses_default_repository_for_remote_subcommands(monkeypatch: pytest.MonkeyPatch) -> None:
    draft_call: dict[str, object] = {}
    audit_call: dict[str, object] = {}

    def fake_create_draft(tag: str, notes_file: Path, *, repository: str) -> None:
        draft_call.update(tag=tag, notes_file=notes_file, repository=repository)

    def fake_audit_remote(repository: str) -> int:
        audit_call["repository"] = repository
        return 3

    monkeypatch.setattr(release_notes, "create_draft", fake_create_draft)
    monkeypatch.setattr(release_notes, "audit_remote", fake_audit_remote)

    assert release_notes.main(["create-draft", "v0.26.0", "release.md"]) == 0
    assert draft_call == {
        "tag": "v0.26.0",
        "notes_file": Path("release.md"),
        "repository": "livingstaccato/octowright",
    }
    assert release_notes.main(["audit-remote"]) == 0
    assert audit_call == {"repository": "livingstaccato/octowright"}


def test_cli_entrypoint_runs_check_body_without_gh(tmp_path: Path) -> None:
    notes = tmp_path / "release.md"
    notes.write_text("Reliable startup.", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/github_release_notes.py", "check-body", str(notes)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "validated release note body" in result.stdout


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
