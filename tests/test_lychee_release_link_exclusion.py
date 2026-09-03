# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The pending release's compare link is unresolvable *by construction*.

A version bump adds ``[X.Y.Z]: .../compare/vPREV...vX.Y.Z`` to CHANGELOG.md, but
the ``vX.Y.Z`` tag is only pushed after the PR merges. So the link 404s for the
entire life of the PR and ``links.yml`` shows a red X on every release PR --
which trains people to wave past a failing gate. It already produced one bad
"fix": the ``[0.11.0]`` link was repointed at ``...main`` to silence the error
and is now permanently wrong.

The exclusion these tests pin is deliberately narrow: only the compare link
whose *target* tag equals the version in the VERSION file. A blanket
``compare/`` exclusion would permanently stop catching a typo'd tag (a
``v0.19.44`` that will never exist), whereas the narrow one defers that check by
exactly one release -- on the next version's PR the same link is no longer
"current" and is checked for real.

These assertions therefore fail if the pattern is widened, if it loses its
anchors, or if the shell-safety validation is dropped.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# A miniature CHANGELOG link-reference block, in the exact shape the real file
# uses. `0.19.4` is the pending release: its tag does not exist yet.
SAMPLE_CHANGELOG = """\
[0.19.4]: https://github.com/livingstaccato/octowright/compare/v0.19.3...v0.19.4
[0.19.3]: https://github.com/livingstaccato/octowright/compare/v0.19.2...v0.19.3
[0.19.2]: https://github.com/livingstaccato/octowright/compare/v0.19.1...v0.19.2
"""


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "_build_lychee_exclusions", ROOT / "ci" / "build_lychee_exclusions.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _urls(body: str) -> list[str]:
    return re.findall(r"https://\S+", body)


def test_the_pending_release_link_is_excluded_and_the_previous_one_is_not() -> None:
    """The whole point: silence one unresolvable link, keep checking the rest.

    Excluding the previous release's link too would be a slow leak -- every
    already-tagged compare URL is a real link that can rot (a repo rename, a
    deleted tag), and those are exactly the ones this workflow exists to catch.
    """
    pattern = re.compile(_load_script().pending_release_exclusion("0.19.4"))
    matched = [url for url in _urls(SAMPLE_CHANGELOG) if pattern.search(url)]
    assert matched == ["https://github.com/livingstaccato/octowright/compare/v0.19.3...v0.19.4"]


def test_a_typod_target_tag_is_still_checked() -> None:
    """The reason this is not a blanket ``compare/`` exclusion.

    A fat-fingered ``v0.19.44`` names a tag that will never exist, so the link
    is broken forever. A blanket exclusion would hide it forever too; the
    version-anchored one lets lychee 404 on it and fail the job, which is the
    behaviour worth keeping.
    """
    pattern = re.compile(_load_script().pending_release_exclusion("0.19.4"))
    assert not pattern.search("https://github.com/livingstaccato/octowright/compare/v0.19.3...v0.19.44")


def test_the_pending_version_on_the_from_side_is_still_checked() -> None:
    """``vX.Y.Z...vNEXT`` is a different link with a different unknown.

    Only the *target* of a compare is the tag that does not exist yet. Matching
    the version anywhere in the URL would also swallow the next release's link,
    whose target tag is the one actually worth verifying then.
    """
    pattern = re.compile(_load_script().pending_release_exclusion("0.19.4"))
    assert not pattern.search("https://github.com/livingstaccato/octowright/compare/v0.19.4...v0.19.5")


def test_non_compare_links_naming_the_version_are_still_checked() -> None:
    """A release/tag URL for the same version is not deferred by this.

    It 404s for the same reason during the PR -- but nothing in the changelog
    generates one automatically, so a hand-written one is far more likely to be
    a real mistake than a chicken-and-egg artifact. Widening the pattern past
    ``/compare/`` would silently adopt that case.
    """
    pattern = re.compile(_load_script().pending_release_exclusion("0.19.4"))
    assert not pattern.search("https://github.com/livingstaccato/octowright/releases/tag/v0.19.4")


def test_the_version_is_treated_as_a_literal_not_a_regex() -> None:
    """Dots in ``0.19.4`` must not be wildcards.

    Unescaped, ``v0.19.4`` matches ``v0x19y4`` and any other same-shaped tag,
    quietly re-widening the exclusion this test file exists to keep narrow.
    """
    pattern = re.compile(_load_script().pending_release_exclusion("0.19.4"))
    assert not pattern.search("https://github.com/livingstaccato/octowright/compare/v0.19.3...v0y19y4")


def test_a_shell_hostile_version_is_refused() -> None:
    """VERSION is repo content, and the pattern lands on a shell command line.

    The workflow interpolates this string into ``lychee ... --exclude '<here>'``,
    so a VERSION file containing a quote would end the argument and start a new
    command. Refusing anything outside a version-shaped charset keeps the
    workflow from having to be the place that gets quoting right.
    """
    script = _load_script()
    for hostile in ("0.19.4'; rm -rf /", "0.19.4 && whoami", "$(id)", "0.19.4\n"):
        with pytest.raises(ValueError):
            script.pending_release_exclusion(hostile)


def test_prerelease_versions_are_accepted() -> None:
    """Refusing them would red-line the links job on every rc bump."""
    pattern = re.compile(_load_script().pending_release_exclusion("0.20.0rc1"))
    assert pattern.search("https://github.com/livingstaccato/octowright/compare/v0.19.4...v0.20.0rc1")


def test_the_repositorys_own_changelog_yields_exactly_the_pending_link() -> None:
    """Run the real pattern over the real corpus, not just a fixture.

    The committed CHANGELOG carries 20+ compare links, which is the only place
    an over-broad pattern shows itself at full scale. ``expected`` is derived by
    plain string comparison rather than by the regex under test, so the two
    disagree if the pattern drifts. Both sides being empty is a pass: a VERSION
    whose changelog entry is not written yet has no link to defer.
    """
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = re.compile(_load_script().pending_release_exclusion(version))

    urls = _urls(changelog)
    expected = [url for url in urls if "/compare/" in url and url.endswith(f"...v{version}")]
    assert [url for url in urls if pattern.search(url)] == expected


def test_main_emits_one_github_output_assignment(capsys: pytest.CaptureFixture[str]) -> None:
    """The step appends stdout straight to ``$GITHUB_OUTPUT``.

    A second line, or a newline inside the value, silently becomes a different
    output variable (or nothing), and the workflow would then pass an empty
    ``--exclude`` to lychee.
    """
    script = _load_script()
    assert script.main() == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    name, _, value = out.strip().partition("=")
    assert name == "exclude"
    assert value == script.pending_release_exclusion((ROOT / "VERSION").read_text(encoding="utf-8").strip())


def test_the_workflow_actually_uses_the_generated_exclusion() -> None:
    """A pattern nobody passes to lychee fixes nothing.

    This is the half of the change that cannot be unit-tested from Python: the
    script's output has to reach the lychee invocation. Pinning the wiring here
    means a workflow refactor that drops the step fails a test instead of
    quietly restoring the red X on every release PR.
    """
    workflow = (ROOT / ".github" / "workflows" / "links.yml").read_text(encoding="utf-8")
    assert "ci/build_lychee_exclusions.py" in workflow
    assert "--exclude" in workflow
