# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The product-name guard (scripts/check_product_name.py).

Prose says "Octowright"; code keeps its own spelling. Each exempt form below is
code-shaped and must pass, a plain prose use must fail, and the real tree must
be clean so the rule cannot regress silently.
"""

from __future__ import annotations

import pytest

from tests._script_module import load_script_module

checker = load_script_module("scripts/check_product_name.py")


@pytest.mark.parametrize(
    "line",
    [
        "Restart octowright when it wedges.",
        "This is octowright's own overlay.",
        "# octowright examples",
        "| A | octowright | done |",
        "Before `code` and after, octowright still counts.",
        "A link [like this](https://example.test) then octowright.",
    ],
)
def test_a_prose_use_is_flagged(line: str) -> None:
    assert checker.findings(line) == [(1, line)]


@pytest.mark.parametrize(
    "line",
    [
        "Octowright is the product.",
        "Run `octowright serve` first.",
        "Run ``octowright `restart` `` first.",
        "See [the repo](https://github.com/livingstaccato/octowright).",
        "Open https://octowright.com/docs now.",
        "<img alt=x src='octowright.png'>",
        "Install octowright-terminal from PyPI.",
        "Import octowright.browser_pool here.",
        "Set octowright_x in the config.",
        "Edit ~/.config/octowright/profiles by hand.",
        "The .octowright/config.yaml file.",
        "Then octowright serve --wait-ready prints the URL.",
        "Or octowright --help for the list.",
        "Mail octowright@example.test.",
        "The mcp__octowright__browser_click tool.",
        "OCTOWRIGHT_PROFILE and OctowrightError are identifiers.",
        "A comment <!-- octowright --> in the middle.",
    ],
)
def test_a_code_shaped_use_passes(line: str) -> None:
    assert checker.findings(line) == []


def test_fenced_blocks_comments_and_front_matter_are_skipped() -> None:
    text = "\n".join(
        [
            "---",
            "name: octowright",
            "---",
            "```bash",
            "octowright is here",
            "```",
            "<!--",
            "Part of octowright.",
            "-->",
            "~~~",
            "octowright again",
            "~~~",
            "Prose about octowright.",
        ]
    )
    assert checker.findings(text) == [(13, "Prose about octowright.")]


def test_subcommands_match_the_cli() -> None:
    import octowright.cli  # noqa: F401 - registers every subcommand on the group
    from octowright.cli._root import cli

    assert set(cli.commands) == checker.SUBCOMMANDS


def test_the_changelog_and_symlinks_are_out_of_scope() -> None:
    names = {path.relative_to(checker.ROOT).as_posix() for path in checker.tracked_markdown()}
    assert "CHANGELOG.md" not in names
    assert "CLAUDE.md" not in names
    assert "AGENTS.md" in names


def test_the_tree_is_clean() -> None:
    assert checker.main() == 0
