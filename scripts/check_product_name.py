# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Flag a lowercase ``octowright`` in Markdown prose; the product name is "Octowright".

In code the name follows the identifier convention instead -- ``octowright
serve``, ``octowright.browser_pool``, ``~/.config/octowright``,
``OCTOWRIGHT_*``, ``OctowrightError`` -- so this deliberately looks only at
prose and skips everything that is code-shaped:

- fenced code blocks, HTML comments and a leading YAML front-matter block;
- inline code spans, link targets (``](...)``), HTML tags, and URLs;
- a word attached to a path or identifier character (``/``, ``-``, ``_``,
  ``@``, ``:``, a leading ``.``, or a ``.`` followed by a name:
  ``octowright-terminal``, ``octowright.cli``, ``.octowright/``);
- the CLI's own command lines (``octowright serve``, ``octowright --help``),
  recognised by the word after it being a subcommand or an option.

Scope is every ``*.md`` file git tracks, except ``CHANGELOG.md``: its old
release entries record what shipped under the wording of the day, and
rewriting history to satisfy a newer style rule is the wrong trade. A symlink
(``CLAUDE.md`` -> ``AGENTS.md``) is skipped, since its target is checked.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = frozenset({"CHANGELOG.md"})

#: ``octowright <subcommand>`` is a command line, not prose. Kept literal so the
#: guard needs no import of the package; `test_subcommands_match_the_cli` pins it.
SUBCOMMANDS = frozenset(
    {
        "cleanup",
        "dashboard",
        "doctor",
        "init",
        "persona",
        "restart",
        "scenario",
        "selftest",
        "serve",
        "skill",
        "takeover",
        "test",
    }
)

_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"(`+).*?\1")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_HTML_TAG = re.compile(r"<[^>\s][^>]*>")
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_WORD = re.compile(r"\boctowright\b")
#: Characters that, touching the word, make it part of a path or identifier.
_ATTACHED = frozenset("/-_@:\\")


def _prose(line: str) -> str:
    # Blanked to the same length, so a match's offsets are the original line's.
    for pattern in (_INLINE_CODE, _LINK_TARGET, _HTML_TAG, _URL):
        line = pattern.sub(lambda m: " " * len(m.group(0)), line)
    return line


def _is_code_shaped(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before in _ATTACHED or after in _ATTACHED or before == ".":
        return True
    if after == "." and end + 1 < len(text) and (text[end + 1].isalnum() or text[end + 1] == "_"):
        return True
    rest = text[end:].lstrip(" ")
    if rest.startswith("-"):
        return True  # an option: ``octowright --help``
    nxt = re.match(r"[a-z-]+", rest)
    return nxt is not None and nxt.group(0) in SUBCOMMANDS


def _outside_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """*line* without its HTML-comment parts, and whether a comment is still open after it."""
    kept: list[str] = []
    while line:
        if in_comment:
            _, closed, line = line.partition("-->")
            in_comment = not closed
        else:
            head, opened, line = line.partition("<!--")
            kept.append(head)
            in_comment = bool(opened)
    return "".join(kept), in_comment


def findings(text: str) -> list[tuple[int, str]]:
    """``(line number, line)`` for every lowercase prose ``octowright`` in *text*."""
    hits: list[tuple[int, str]] = []
    lines = text.splitlines()
    in_fence = False
    in_comment = False
    index = 0
    if lines and lines[0].strip() == "---":  # YAML front matter
        index = next((i + 1 for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
    for number, line in enumerate(lines[index:], start=index + 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line, in_comment = _outside_comments(line, in_comment)
        prose = _prose(line)
        if any(not _is_code_shaped(prose, m.start(), m.end()) for m in _WORD.finditer(prose)):
            hits.append((number, line.strip()))
    return hits


def tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    paths = [ROOT / name for name in out.split("\0") if name]
    return [p for p in paths if p.name not in EXCLUDED and not p.is_symlink() and p.is_file()]


def main() -> int:
    failed = 0
    for path in tracked_markdown():
        for number, line in findings(path.read_text(encoding="utf-8")):
            print(f"{path.relative_to(ROOT)}:{number}: lowercase 'octowright' in prose: {line}")
            failed += 1
    if failed:
        print(
            f"\n{failed} line(s) use a lowercase 'octowright' in prose. The product name is 'Octowright'; "
            "keep lowercase only in code (commands, module paths, file paths, URLs, identifiers), "
            "and mark code as code with backticks."
        )
        return 1
    print("Markdown prose spells the product name 'Octowright'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
