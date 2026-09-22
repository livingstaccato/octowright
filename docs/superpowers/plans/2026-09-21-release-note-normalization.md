# GitHub Release Note Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Normalize every Octowright GitHub release title to its `vX.Y.Z` tag, remove issue-number references from every release-note surface, and add a tested draft-release path that prevents the drift from returning.

**Architecture:** Put release-note policy, local validation, remote auditing, and draft creation in one standard-library Python script with a small command-line interface. Clean the current changelog, wire local validation into `make lint`, merge those safeguards before mutating GitHub, then perform a snapshot-backed one-time migration of all 40 releases and verify the live API. The already-approved octowright.com sync remains a separate downstream plan and starts only after this plan's remote audit passes.

**Tech Stack:** Python 3.11+, pytest, GitHub CLI, Git/GitHub Actions, Markdown/JSON release data

---

## File map

- Create `scripts/github_release_notes.py`: issue-reference detection, local source validation, GitHub release audit, and safe draft creation.
- Create `tests/test_github_release_notes.py`: focused unit and wiring tests for the script.
- Modify `CHANGELOG.md`: remove all issue-number references while retaining their explanatory prose.
- Create `docs/releasing.md`: canonical release-note policy and draft/publish procedure.
- Modify `docs/README.md`: link the new release guide.
- Modify `docs/ci-quality.md`: document the new lint guard.
- Modify `Makefile`: run the local release-note check in `make lint`.
- Modify `.github/workflows/release.yml`: point maintainers at the required draft helper without changing the publication trigger.
- External state: normalize 40 GitHub release titles and reapply 40 audited bodies through `gh release edit`.
- Downstream plan: `/Users/tim/code/gh/provide-io/site-octowright-com/.worktrees/release-0.25.0-site-sync/docs/superpowers/plans/2026-09-21-octowright-0.25.0-site-sync.md`.

### Task 1: Build the release-note policy and local-source guard with TDD

**Files:**
- Create: `tests/test_github_release_notes.py`
- Create: `scripts/github_release_notes.py`

- [ ] **Step 1: Write failing tests for prohibited references and clean prose**

Create `tests/test_github_release_notes.py` with the repository SPDX header and these first tests:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._script_module import load_script_module


def _module():
    return load_script_module("scripts/github_release_notes.py")


@pytest.mark.parametrize(
    "text",
    (
        "Issue #247 is fixed.",
        "Issue 247 is fixed.",
        "Known limitation (#247).",
        "#247: short values are rewritten.",
        "See https://github.com/livingstaccato/octowright/issues/247.",
    ),
)
def test_release_notes_reject_issue_references(text: str) -> None:
    with pytest.raises(ValueError, match="issue reference"):
        _module().validate_notes(text, source="notes.md")


@pytest.mark.parametrize(
    "text",
    (
        "Short identity values no longer corrupt macro recordings.",
        "CVE-2026-62949 is addressed.",
        "### Fixed\n\nThe failure now names its cause.",
        "Release v0.25.0 contains two compatibility fixes.",
    ),
)
def test_release_notes_accept_self_contained_prose(text: str) -> None:
    _module().validate_notes(text, source="notes.md")


def test_release_notes_reject_an_empty_body() -> None:
    with pytest.raises(ValueError, match="empty"):
        _module().validate_notes(" \n", source="notes.md")
```

- [ ] **Step 2: Run the tests and verify the script is missing**

Run:

```bash
uv run pytest -q tests/test_github_release_notes.py
```

Expected: failure while loading the nonexistent `scripts/github_release_notes.py`.

- [ ] **Step 3: Add the minimal validator implementation**

Create `scripts/github_release_notes.py` with the repository SPDX header, then add:

```python
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPOSITORY = "livingstaccato/octowright"
TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")
ISSUE_REFERENCE_RE = re.compile(
    r"(?ix)(?:"
    r"https?://github\.com/[^\s/]+/[^\s/]+/issues/[0-9]+\b"
    r"|\bissues?\s*#?\s*[0-9]+\b"
    r"|(?<![\w/])#[0-9]+\b"
    r")"
)


def validate_notes(text: str, *, source: str) -> None:
    if not text.strip():
        raise ValueError(f"{source}: release notes are empty")
    match = ISSUE_REFERENCE_RE.search(text)
    if match is not None:
        line = text.count("\n", 0, match.start()) + 1
        raise ValueError(f"{source}:{line}: release notes contain issue reference {match.group(0)!r}")
```

Keep the imported modules needed by later tasks even if ruff reports them as unused until Task 2; do not commit between the intentionally red and green states.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest -q tests/test_github_release_notes.py
```

Expected: all current tests pass.

- [ ] **Step 5: Add failing tests for repository-wide source validation**

Append:

```python
def test_validate_local_sources_checks_changelog_and_every_highlight(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text("Context-rich release notes.\n", encoding="utf-8")
    highlights = tmp_path / "src" / "octowright" / "upgrade" / "highlights"
    highlights.mkdir(parents=True)
    (highlights / "0.25.0.json").write_text(
        json.dumps([{"title": "Good title", "body": "Issue #247 is fixed."}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"0\.25\.0\.json"):
        _module().validate_local_sources(tmp_path)


def test_validate_local_sources_accepts_clean_files(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text("Context-rich release notes.\n", encoding="utf-8")
    highlights = tmp_path / "src" / "octowright" / "upgrade" / "highlights"
    highlights.mkdir(parents=True)
    (highlights / "0.25.0.json").write_text(
        json.dumps([{"title": "Good title", "body": "The failure now names its cause."}]),
        encoding="utf-8",
    )

    assert _module().validate_local_sources(tmp_path) == 2
```

- [ ] **Step 6: Run the new tests and verify the function is missing**

Run:

```bash
uv run pytest -q tests/test_github_release_notes.py -k local_sources
```

Expected: failures because `validate_local_sources` is not defined.

- [ ] **Step 7: Implement local-source validation**

Add:

```python
def local_note_paths(repo_root: Path) -> list[Path]:
    highlights = repo_root / "src" / "octowright" / "upgrade" / "highlights"
    return [repo_root / "CHANGELOG.md", *sorted(highlights.glob("*.json"))]


def validate_local_sources(repo_root: Path = REPO_ROOT) -> int:
    paths = local_note_paths(repo_root)
    for path in paths:
        validate_notes(path.read_text(encoding="utf-8"), source=str(path))
    return len(paths)
```

- [ ] **Step 8: Run the full focused file and commit Task 1**

Run:

```bash
uv run pytest -q tests/test_github_release_notes.py
git diff --check
git add scripts/github_release_notes.py tests/test_github_release_notes.py
git commit -m "feat(release): validate context-rich release notes"
```

Expected: focused tests pass and the commit hooks succeed.

### Task 2: Add safe draft creation and remote auditing with TDD

**Files:**
- Modify: `tests/test_github_release_notes.py`
- Modify: `scripts/github_release_notes.py`

- [ ] **Step 1: Add failing tests for tag validation and exact draft command construction**

Append tests using a recording runner:

```python
class RecordingRunner:
    def __init__(self, *, stdout: str = "") -> None:
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr="")


def test_create_draft_uses_the_tag_as_the_only_title(tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("A self-contained release body.\n", encoding="utf-8")
    runner = RecordingRunner()

    _module().create_draft("v0.26.0", notes, repository="owner/repo", runner=runner)

    assert runner.calls == [[
        "gh", "release", "create", "v0.26.0",
        "--repo", "owner/repo",
        "--title", "v0.26.0",
        "--notes-file", str(notes),
        "--draft", "--verify-tag",
    ]]


def test_create_draft_validates_before_running_gh(tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("Issue #247 is fixed.\n", encoding="utf-8")
    runner = RecordingRunner()

    with pytest.raises(ValueError, match="issue reference"):
        _module().create_draft("v0.26.0", notes, repository="owner/repo", runner=runner)

    assert runner.calls == []


@pytest.mark.parametrize("tag", ("0.26.0", "v0.26", "vnext", "v0.26.0\n--latest"))
def test_create_draft_rejects_a_non_release_tag(tag: str, tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("A self-contained release body.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="release tag"):
        _module().create_draft(tag, notes, repository="owner/repo", runner=RecordingRunner())
```

Add `import subprocess` and `from typing import Any` to the test file.

- [ ] **Step 2: Run the draft tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_github_release_notes.py -k create_draft
```

Expected: failures because `create_draft` is not defined.

- [ ] **Step 3: Implement validated draft creation**

Add these functions to the script:

```python
def validate_tag(tag: str) -> None:
    if TAG_RE.fullmatch(tag) is None:
        raise ValueError(f"invalid release tag: {tag!r}; expected vX.Y.Z")


def create_draft(
    tag: str,
    notes_file: Path,
    *,
    repository: str = DEFAULT_REPOSITORY,
    runner: Any = subprocess.run,
) -> None:
    validate_tag(tag)
    body = notes_file.read_text(encoding="utf-8")
    validate_notes(body, source=str(notes_file))
    runner(
        [
            "gh", "release", "create", tag,
            "--repo", repository,
            "--title", tag,
            "--notes-file", str(notes_file),
            "--draft", "--verify-tag",
        ],
        check=True,
        text=True,
    )  # nosec B603 B607
```

- [ ] **Step 4: Add failing tests for live-release audit rules**

Append:

```python
def test_audit_remote_requires_tag_titles_nonempty_bodies_and_clean_notes() -> None:
    clean = json.dumps([[
        {"tag_name": "v0.25.0", "name": "v0.25.0", "body": "Context-rich notes."},
        {"tag_name": "v0.24.0", "name": "v0.24.0", "body": "Older context-rich notes."},
    ]])
    assert _module().audit_remote("owner/repo", runner=RecordingRunner(stdout=clean)) == 2

    wrong_title = json.dumps([[
        {"tag_name": "v0.25.0", "name": "octowright 0.25.0", "body": "Context-rich notes."},
    ]])
    with pytest.raises(ValueError, match="title"):
        _module().audit_remote("owner/repo", runner=RecordingRunner(stdout=wrong_title))

    issue_body = json.dumps([[
        {"tag_name": "v0.25.0", "name": "v0.25.0", "body": "Issue #247 is fixed."},
    ]])
    with pytest.raises(ValueError, match="issue reference"):
        _module().audit_remote("owner/repo", runner=RecordingRunner(stdout=issue_body))
```

- [ ] **Step 5: Run the audit tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_github_release_notes.py -k audit_remote
```

Expected: failures because `audit_remote` is not defined.

- [ ] **Step 6: Implement GitHub fetching and audit**

Add:

```python
def fetch_releases(repository: str, *, runner: Any = subprocess.run) -> list[dict[str, Any]]:
    completed = runner(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603 B607
    pages = json.loads(completed.stdout)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError("GitHub releases response is not a list of pages")
    return [release for page in pages for release in page]


def audit_remote(repository: str = DEFAULT_REPOSITORY, *, runner: Any = subprocess.run) -> int:
    releases = fetch_releases(repository, runner=runner)
    for release in releases:
        tag = str(release.get("tag_name", ""))
        title = str(release.get("name", ""))
        if title != tag:
            raise ValueError(f"{tag}: release title {title!r} must equal its tag")
        validate_notes(str(release.get("body", "")), source=f"GitHub release {tag}")
    return len(releases)
```

- [ ] **Step 7: Add the command-line interface and its focused tests**

Add a `main(argv: Sequence[str] | None = None) -> int` with four subcommands:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check-local", help="validate committed changelog and upgrade highlights")

    check_body = subparsers.add_parser("check-body", help="validate one Markdown release body")
    check_body.add_argument("notes_file", type=Path)

    create = subparsers.add_parser("create-draft", help="create a validated draft GitHub release")
    create.add_argument("tag")
    create.add_argument("notes_file", type=Path)
    create.add_argument("--repo", default=DEFAULT_REPOSITORY)

    audit = subparsers.add_parser("audit-remote", help="validate all live GitHub releases")
    audit.add_argument("--repo", default=DEFAULT_REPOSITORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check-local":
            count = validate_local_sources()
            print(f"validated {count} local release-note sources")
        elif args.command == "check-body":
            validate_notes(args.notes_file.read_text(encoding="utf-8"), source=str(args.notes_file))
            print(f"validated {args.notes_file}")
        elif args.command == "create-draft":
            create_draft(args.tag, args.notes_file, repository=args.repo)
        else:
            count = audit_remote(args.repo)
            print(f"validated {count} GitHub releases")
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add `import sys` to the script. Test `main(["check-body", ...])`, invalid-body exit 1, and parser default repository without invoking live GitHub.

- [ ] **Step 8: Run all focused tests, lint the two files, and commit Task 2**

Run:

```bash
uv run pytest -q tests/test_github_release_notes.py
uv run ruff check scripts/github_release_notes.py tests/test_github_release_notes.py
uv run ruff format --check scripts/github_release_notes.py tests/test_github_release_notes.py
git diff --check
git add scripts/github_release_notes.py tests/test_github_release_notes.py
git commit -m "feat(release): prepare and audit GitHub releases"
```

Expected: focused tests and lint pass; commit hooks succeed.

### Task 3: Clean source release notes and wire the permanent policy

**Files:**
- Modify: `CHANGELOG.md`
- Create: `docs/releasing.md`
- Modify: `docs/README.md`
- Modify: `docs/ci-quality.md`
- Modify: `Makefile`
- Modify: `.github/workflows/release.yml`
- Test: `tests/test_github_release_notes.py`

- [ ] **Step 1: Prove the new local guard fails on the current changelog**

Run:

```bash
uv run python scripts/github_release_notes.py check-local
```

Expected: exit 1 naming the first issue reference in `CHANGELOG.md`.

- [ ] **Step 2: Remove every issue-number reference from the current changelog**

Use `apply_patch` for these exact content changes while retaining surrounding context:

- remove parentheticals `(#247)`, `(#240)`, `(#234)`, `(#235)`, and `(#187)`;
- replace `Related: #247, #248.` with: `Known limitations remain: short or common classified values can rewrite unrelated recording rows, and some macro parameter metadata does not yet reach every privacy boundary.`;
- change `recording (#247)` to `recording`;
- replace `The unimplemented privacy design work is tracked in #248.` with `Some planned macro-parameter privacy work remains unimplemented: privacy metadata does not yet reach every execution path, and typed parameter specifications remain incomplete.`;
- replace `a pre-rebase commit from #232's branch` with `a pre-rebase branch commit`;
- in the 0.23.0 structural-gaps paragraph, remove `(#234)` and `(#235)` while preserving both gap descriptions.

Run:

```bash
rg -n '#[0-9]+|issues?\s+#?[0-9]+' CHANGELOG.md src/octowright/upgrade/highlights || true
uv run python scripts/github_release_notes.py check-local
```

Expected: ripgrep prints nothing and the guard reports 47 validated sources (one changelog plus 46 highlight files).

- [ ] **Step 3: Add the canonical release procedure**

Create `docs/releasing.md` with these required sections and commands:

````markdown
# Releasing Octowright

GitHub release titles are exactly their tags (`vX.Y.Z`). Release bodies must
explain each user-visible problem and result without issue numbers or issue
links. Issues remain engineering coordination; they are not release-note
context.

## Prepare and validate notes

Write the release body in a temporary Markdown file, then run:

```bash
uv run python scripts/github_release_notes.py check-body /tmp/octowright-release-notes.md
uv run python scripts/github_release_notes.py check-local
```

## Create the draft

After the version commit and tag exist locally and remotely:

```bash
uv run python scripts/github_release_notes.py create-draft vX.Y.Z /tmp/octowright-release-notes.md
```

The helper derives the title from the tag and creates a verified draft. Review
the title, body, tag, and attached artifacts on GitHub before publishing it.
Publishing—not draft creation—triggers `.github/workflows/release.yml` and the
PyPI/TestPyPI upload.

## Verify public history

```bash
uv run python scripts/github_release_notes.py audit-remote
```
````

Add a `Releases` entry pointing to `releasing.md` in `docs/README.md`.

- [ ] **Step 4: Wire and document the local guard**

Add this line to `Makefile` after the existing agent-docs guard:

```make
	uv run python scripts/github_release_notes.py check-local
```

Add a row to the guard table in `docs/ci-quality.md`:

```markdown
| `github_release_notes.py check-local` | the changelog or a curated upgrade highlight is empty or contains an issue-number reference instead of standalone context. |
```

Add a comment near the `on: release` trigger in `.github/workflows/release.yml` pointing to `docs/releasing.md` and stating that maintainers must create drafts through `scripts/github_release_notes.py` so titles and bodies are validated before publication. Do not change the trigger or permissions.

- [ ] **Step 5: Add wiring tests before running the full gate**

Append tests asserting:

```python
def test_the_committed_release_note_sources_are_clean() -> None:
    assert _module().validate_local_sources(Path(__file__).resolve().parents[1]) == 47


def test_make_lint_runs_the_release_note_guard() -> None:
    makefile = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")
    assert "python scripts/github_release_notes.py check-local" in makefile


def test_release_docs_require_the_safe_draft_helper() -> None:
    root = Path(__file__).resolve().parents[1]
    guide = (root / "docs" / "releasing.md").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "github_release_notes.py create-draft" in guide
    assert "docs/releasing.md" in workflow
```

- [ ] **Step 6: Run focused policy checks and commit Task 3**

Run:

```bash
uv run pytest -q tests/test_github_release_notes.py
uv run python scripts/github_release_notes.py check-local
uv run codespell CHANGELOG.md docs/releasing.md docs/README.md docs/ci-quality.md scripts/github_release_notes.py tests/test_github_release_notes.py .github/workflows/release.yml
git diff --check
git add CHANGELOG.md docs/releasing.md docs/README.md docs/ci-quality.md Makefile .github/workflows/release.yml tests/test_github_release_notes.py
git commit -m "docs(release): require standalone release-note context"
```

Expected: all focused checks pass and commit hooks succeed.

### Task 4: Verify, review, merge, and deploy the repository safeguards

**Files:**
- Verify all files changed in Tasks 1–3.

- [ ] **Step 1: Run the complete local quality gate**

Run:

```bash
PIPAPI_PYTHON_LOCATION="$PWD/.venv/bin/python" make ci
```

Expected: lint, types, security/doc guards, audit, non-memory tests with coverage, and the isolated memory suite all pass.

- [ ] **Step 2: Run release-helper smoke checks without remote writes**

Run:

```bash
uv run python scripts/github_release_notes.py check-local
uv run python scripts/github_release_notes.py audit-remote
git diff --check
git status --short --branch
```

Expected: local validation passes; remote audit fails only on the known pre-migration title/body drift; the worktree has no uncommitted tracked changes.

- [ ] **Step 3: Push and open the Octowright pull request**

Run:

```bash
git push -u origin codex/release-note-normalization
gh pr create --base main --head codex/release-note-normalization --title "Normalize GitHub release notes" --body-file /tmp/octowright-release-note-normalization-pr.md
```

Create the PR body with `apply_patch`; include the policy, five affected historical bodies, no-tag/no-package-mutation guarantee, and exact verification results. Attach the returned PR URL to the Codex task.

- [ ] **Step 4: Require PR review and all checks**

Run:

```bash
gh pr checks --watch
gh pr view --json mergeStateStatus,reviewDecision,statusCheckRollup,url
```

Expected: every required check and review passes. Fix failures on the branch and rerun both review stages before proceeding.

- [ ] **Step 5: Merge the reviewed safeguards**

Run:

```bash
gh pr merge --squash --delete-branch
```

Expected: the PR merges to `main`. Record the merge SHA. This merge does not create a release or publish a package.

### Task 5: Snapshot, rewrite, and normalize all 40 live GitHub releases

**Files:**
- Temporary backup: a unique `/tmp/octowright-release-notes.*` directory.
- External state: `livingstaccato/octowright` GitHub releases.

- [ ] **Step 1: Create a unique migration directory and immutable pre-write snapshot**

Run:

```bash
release_migration_dir=$(mktemp -d /tmp/octowright-release-notes.XXXXXX)
printf '%s\n' "$release_migration_dir" > /tmp/octowright-release-note-normalization.path
gh api 'repos/livingstaccato/octowright/releases?per_page=100' > "$release_migration_dir/before.json"
mkdir "$release_migration_dir/bodies"
RELEASE_MIGRATION_DIR="$release_migration_dir" python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["RELEASE_MIGRATION_DIR"])
releases = json.loads((root / "before.json").read_text(encoding="utf-8"))
assert len(releases) == 40, len(releases)
for release in releases:
    tag = release["tag_name"]
    assert tag.startswith("v") and "/" not in tag
    body = release.get("body") or ""
    assert body.strip(), tag
    (root / "bodies" / f"{tag}.md").write_text(body, encoding="utf-8")
print(f"snapshotted {len(releases)} releases in {root}")
PY
```

Expected: exactly 40 non-empty body files plus `before.json`. Keep this directory until every final verification passes.

- [ ] **Step 2: Rewrite only the five issue-bearing body files**

Use `apply_patch` against the absolute temporary paths and make these exact semantic changes:

- `v0.25.0.md`: replace `Issue #247 is fixed.` with `Short identity and context values no longer corrupt unrelated macro-recording content.`;
- `v0.24.0.md`: remove `(#240)`, `(#234)`, and `(#235)` from the three descriptive fixes; replace `#247: the session privacy ledger rewrites short classified values in unrelated recording rows` with `Short classified values can cause the session privacy ledger to rewrite unrelated recording rows`; replace `#248: macro parameter privacy, remaining Part 0 threading and Part B parameter_specs` with `Some macro parameter privacy metadata does not yet reach every execution path, and typed parameter specifications remain incomplete`;
- `v0.23.0.md`: remove the `#234`, `#235`, and `#240` prefixes from the three already-descriptive known-gap bullets;
- `v0.19.4.md`: remove the trailing `(#187)` from the descriptive failure paragraph;
- `v0.19.3.md`: remove `(#180)`, `(#181, #182)` from the two descriptive bullets.

Do not change any other body file.

- [ ] **Step 3: Validate the full proposal before any remote write**

Run:

```bash
release_migration_dir=$(cat /tmp/octowright-release-note-normalization.path)
RELEASE_MIGRATION_DIR="$release_migration_dir" uv run python - <<'PY'
import json
import os
from pathlib import Path

from tests._script_module import load_script_module

tool = load_script_module("scripts/github_release_notes.py")
root = Path(os.environ["RELEASE_MIGRATION_DIR"])
before = json.loads((root / "before.json").read_text(encoding="utf-8"))
expected_changed = {"v0.19.3", "v0.19.4", "v0.23.0", "v0.24.0", "v0.25.0"}
seen: set[str] = set()
changed: set[str] = set()
for release in before:
    tag = release["tag_name"]
    seen.add(tag)
    proposal = (root / "bodies" / f"{tag}.md").read_text(encoding="utf-8")
    tool.validate_notes(proposal, source=tag)
    if proposal != (release.get("body") or ""):
        changed.add(tag)
assert len(seen) == 40
assert changed == expected_changed, changed
print("validated 40 titles and bodies; exactly five bodies changed")
PY
```

Expected: `validated 40 titles and bodies; exactly five bodies changed`.

- [ ] **Step 4: Record the latest package-release workflow run before editing metadata**

Run:

```bash
release_migration_dir=$(cat /tmp/octowright-release-note-normalization.path)
gh run list --repo livingstaccato/octowright --workflow release.yml --limit 1 --json databaseId,headSha,conclusion,createdAt > "$release_migration_dir/release-run-before.json"
```

Expected: the existing v0.25.0 publication run is recorded for the no-republish check.

- [ ] **Step 5: Apply every title and audited body one release at a time**

Run:

```bash
release_migration_dir=$(cat /tmp/octowright-release-note-normalization.path)
RELEASE_MIGRATION_DIR="$release_migration_dir" python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path

root = Path(os.environ["RELEASE_MIGRATION_DIR"])
releases = json.loads((root / "before.json").read_text(encoding="utf-8"))
for release in sorted(releases, key=lambda item: item["tag_name"]):
    tag = release["tag_name"]
    body = root / "bodies" / f"{tag}.md"
    subprocess.run(
        ["gh", "release", "edit", tag, "--repo", "livingstaccato/octowright", "--title", tag, "--notes-file", str(body)],
        check=True,
    )
    print(f"updated {tag}")
PY
```

Expected: 40 successful `updated vX.Y.Z` lines. On a failure, stop; read that tag's live state and retry only the failed and subsequent tags.

- [ ] **Step 6: Fetch fresh state and prove the migration is exact**

Run:

```bash
release_migration_dir=$(cat /tmp/octowright-release-note-normalization.path)
gh api 'repos/livingstaccato/octowright/releases?per_page=100' > "$release_migration_dir/after.json"
uv run python scripts/github_release_notes.py audit-remote
RELEASE_MIGRATION_DIR="$release_migration_dir" python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["RELEASE_MIGRATION_DIR"])
before = json.loads((root / "before.json").read_text(encoding="utf-8"))
after = json.loads((root / "after.json").read_text(encoding="utf-8"))
before_by_tag = {item["tag_name"]: item for item in before}
after_by_tag = {item["tag_name"]: item for item in after}
assert before_by_tag.keys() == after_by_tag.keys()
assert len(after_by_tag) == 40
for tag, release in after_by_tag.items():
    assert release["name"] == tag
    expected_body = (root / "bodies" / f"{tag}.md").read_text(encoding="utf-8")
    assert release["body"] == expected_body, tag
    assert release["tag_name"] == before_by_tag[tag]["tag_name"]
    assert release["target_commitish"] == before_by_tag[tag]["target_commitish"]
print("verified all 40 live releases against the proposal")
PY
```

Expected: the helper validates 40 releases and the comparison prints `verified all 40 live releases against the proposal`.

- [ ] **Step 7: Prove metadata edits did not republish packages**

Run:

```bash
release_migration_dir=$(cat /tmp/octowright-release-note-normalization.path)
gh run list --repo livingstaccato/octowright --workflow release.yml --limit 1 --json databaseId,headSha,conclusion,createdAt > "$release_migration_dir/release-run-after.json"
diff -u "$release_migration_dir/release-run-before.json" "$release_migration_dir/release-run-after.json"
```

Expected: no diff; editing release metadata did not emit a new `published` event or rerun package publication.

- [ ] **Step 8: Retain rollback evidence and report the remote migration**

Do not delete the temporary snapshot during this task. Report its path, the 40/40 clean audit, the five materially changed bodies, and the no-republish comparison. If any remote verification failed, use `before.json` and the per-tag body files to restore only the affected release before proceeding.

### Task 6: Resume the approved octowright.com plan

**Files:**
- Execute the separate site plan: `/Users/tim/code/gh/provide-io/site-octowright-com/.worktrees/release-0.25.0-site-sync/docs/superpowers/plans/2026-09-21-octowright-0.25.0-site-sync.md`.

- [ ] **Step 1: Reconfirm the downstream source invariant**

Run in the Octowright repository:

```bash
uv run python scripts/github_release_notes.py check-local
uv run python scripts/github_release_notes.py audit-remote
```

Expected: 47 local sources and 40 live releases pass before website data is regenerated.

- [ ] **Step 2: Execute the existing site plan from Task 1 onward**

Use the selected subagent-driven workflow. Its release-only generator must add 0.23.0, 0.24.0, and 0.25.0 without touching demo assets; its local build must render the v0.25.0 badge and newest-first releases; its PR must pass checks and merge; and its `main` deployment must pass cache-safe production verification.

- [ ] **Step 3: Report the complete cross-repository result**

Report both PR URLs and merge SHAs, the 40-release audit, Octowright test/quality results, website test/build/link results, Cloudflare deployment run, production build stamp, and live homepage/Releases URLs. Note any local-only check that was unavailable even when its required CI equivalent passed.
