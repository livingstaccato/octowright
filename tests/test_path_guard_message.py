# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``reject_unsafe_path``'s rejection message: named once, typed as a refusal.

Observed against the live daemon on a rejected screenshot::

    screenshot path '/tmp/x.png' '/tmp/x.png' resolves outside '/Users/.../sessions'

The helper appends the path, and four of twenty call sites also interpolated it
into ``label=``. The dedupe lives in the helper rather than in the four labels
because ``label`` is forwarded verbatim through wrappers
(``artifacts.paths.ArtifactStore._contained``), so where a label is built and
where it is rendered are different modules -- a scan of the call sites cannot
see the forwarded case, and an AST guard written for it was deleted in favour
of this.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright._paths import reject_unsafe_path
from octowright.request_errors import InvalidRequestError


@pytest.mark.parametrize(
    "label",
    [
        "screenshot path",
        # The four drifted call sites' spelling, and the forwarded-label case
        # a call-site scan cannot reach.
        "screenshot path '/tmp/x.png'",
    ],
)
def test_the_path_is_named_exactly_once(tmp_path: Path, label: str) -> None:
    with pytest.raises(InvalidRequestError) as excinfo:
        reject_unsafe_path(Path("/tmp/x.png"), tmp_path, label=label)

    message = str(excinfo.value)
    assert message.count("/tmp/x.png") == 1, message
    assert message.startswith("screenshot path '/tmp/x.png' resolves outside "), message


def test_a_label_naming_a_distinct_input_is_left_alone(tmp_path: Path) -> None:
    """The dedupe keys on the rendered PATH, not on a path-ish looking label.

    ``macro name 'x'`` names the caller's input, which is not the resolved
    path, and is exactly the context a bare ``"macro name"`` would lose.
    """
    with pytest.raises(InvalidRequestError) as excinfo:
        reject_unsafe_path(tmp_path.parent / "escape.json", tmp_path, label="macro name '../escape'")

    message = str(excinfo.value)
    assert "macro name '../escape'" in message
    assert str(tmp_path.parent / "escape.json") in message


def test_a_contained_path_is_returned_resolved(tmp_path: Path) -> None:
    """Guard the guard: a helper that always raised would pass the tests above."""
    inside = tmp_path / "sub" / "ok.png"
    assert reject_unsafe_path(inside, tmp_path, label="screenshot path") == inside.resolve()
