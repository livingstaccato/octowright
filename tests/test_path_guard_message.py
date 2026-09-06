# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``reject_unsafe_path``'s rejection message: named once, typed as a refusal.

Observed against the live daemon on a rejected screenshot::

    screenshot path '/tmp/x.png' '/tmp/x.png' resolves outside '/Users/.../sessions'

The helper appends the path, and four of twenty call sites also interpolated it
into ``label=``. Fixed at the call sites and pinned by the scan below, rather
than deduped inside the helper: a dedupe has to decide whether an occurrence in
the label IS the rendered path, and the cheap spelling (``shown in label``)
gets that wrong in the direction that loses information -- for candidate ``x``
and label ``macro name 'xylophone'`` it matches and drops the path entirely.

The scan is intraprocedural and only sees a literal f-string ``label=`` on a
direct call, which is sound because ``label`` is also forwarded through
wrappers (``artifacts.paths.ArtifactStore._contained``) and every forwarded
label names a DISTINCT input -- ``macro artifact {name!r}``, ``artifact run
{run_id!r}``, ``macro digest recording path`` -- which is the useful case the
rule deliberately allows. If a forwarded label ever interpolates a resolved
path, this cannot catch it; that is stated rather than papered over.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from octowright._paths import reject_unsafe_path
from octowright.request_errors import InvalidRequestError

SRC = Path(__file__).resolve().parent.parent / "src" / "octowright"

#: Source is read as UTF-8 explicitly. ``read_text()`` uses the LOCALE codec,
#: which is cp1252 on the Windows runners, and this tree's sources carry UTF-8
#: em-dashes -- so the bare call fails the scan with a ``UnicodeDecodeError``
#: that says nothing about labels. Observed on both Windows legs of CI.
SOURCE_ENCODING = "utf-8"


def _normalize(node: ast.expr) -> str:
    """Unparse ``node``, seeing through a ``str(...)`` wrapper.

    ``label=f"... {str(target)!r}"`` and ``candidate=target`` are the same
    value; without this the most common spelling of the bug reads as a
    different expression and slips through.
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" and len(node.args) == 1:
        node = node.args[0]
    return ast.unparse(node)


def _offenders(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "reject_unsafe_path" or not node.args:
            continue
        label = next((kw.value for kw in node.keywords if kw.arg == "label"), None)
        if not isinstance(label, ast.JoinedStr):
            continue
        candidate = _normalize(node.args[0])
        for part in label.values:
            if isinstance(part, ast.FormattedValue) and _normalize(part.value) == candidate:
                found.append((node.lineno, ast.unparse(label)))
    return found


def test_no_label_restates_the_candidate_path() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for lineno, label in _offenders(ast.parse(path.read_text(encoding=SOURCE_ENCODING), filename=str(path))):
            offenders.append(f"{path.relative_to(SRC.parent.parent)}:{lineno}: label={label}")

    assert not offenders, (
        "reject_unsafe_path already appends the candidate path to its message; "
        "these labels print it a second time. Name the argument instead "
        '(label="screenshot path"):\n  ' + "\n  ".join(offenders)
    )


def test_the_scan_would_catch_the_bug_it_was_written_for() -> None:
    """Guard the guard: a scan that matches nothing passes for the wrong reason."""
    bad = ast.parse('reject_unsafe_path(target, ROOT, label=f"screenshot path {str(target)!r}")')
    good = ast.parse('reject_unsafe_path(ROOT / f"{name}.json", ROOT, label=f"macro name {name!r}")')
    plain = ast.parse('reject_unsafe_path(target, ROOT, label="screenshot path")')

    assert _offenders(bad)
    assert not _offenders(good)
    assert not _offenders(plain)


def test_the_path_is_named_exactly_once(tmp_path: Path) -> None:
    # Derived from tmp_path rather than written as a POSIX literal: on Windows
    # `str(Path("/tmp/x.png"))` is `\tmp\x.png`, so a literal assertion fails
    # for a spelling reason that has nothing to do with what is under test.
    outside = tmp_path.parent / "escape-x.png"

    with pytest.raises(InvalidRequestError) as excinfo:
        reject_unsafe_path(outside, tmp_path, label="screenshot path")

    # Compared against the RENDERED form. The message interpolates the path
    # with `!r`, which escapes backslashes, so on Windows the message holds
    # `'C:\\Users\\...'` while `str(outside)` is the single-backslash raw
    # string and the count is 0. Counting the repr is exact on both platforms
    # and still catches a doubled path, which would render it twice.
    shown = f"{str(outside)!r}"

    message = str(excinfo.value)
    assert message.count(shown) == 1, message
    assert message.startswith(f"screenshot path {shown} resolves outside "), message


def test_a_contained_path_is_returned_resolved(tmp_path: Path) -> None:
    """Guard the guard: a helper that always raised would pass the test above."""
    inside = tmp_path / "sub" / "ok.png"
    assert reject_unsafe_path(inside, tmp_path, label="screenshot path") == inside.resolve()
