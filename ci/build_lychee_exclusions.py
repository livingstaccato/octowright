# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Emit the one lychee exclusion a release PR cannot avoid needing.

A version bump adds ``[X.Y.Z]: .../compare/vPREV...vX.Y.Z`` to CHANGELOG.md,
but the ``vX.Y.Z`` tag is only pushed once the PR merges. The link is therefore
unresolvable *by construction* for the whole life of the PR, and ``links.yml``
red-Xes every release PR on it -- which teaches people that a failing gate is
normal. It has already produced one bad "fix": the ``[0.11.0]`` link was
repointed at ``...main`` to silence the error and is permanently wrong.

**Why this is not a blanket ``compare/`` exclusion.** A blanket rule would also
stop catching a typo'd tag (``v0.19.44``), forever. Anchoring on the version in
the VERSION file defers the check by exactly one release instead of dropping
it: while a release is pending, its own link is unresolvable and is skipped; on
the *next* release's PR that same link is no longer the current version, so it
is checked for real. Deferred one cycle, not abandoned.

Prints a single ``exclude=<regex>`` line on stdout for the calling step to
append to ``$GITHUB_OUTPUT``. Lives in ``ci/`` rather than inline in the
workflow because CLAUDE.md prohibits ``run:`` blocks longer than three lines.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# VERSION is ordinary repo content that a PR can edit, and this pattern is
# interpolated into a shell command line (``lychee ... --exclude '<here>'``), so
# anything outside a version-shaped charset is refused here rather than left for
# the workflow's quoting to survive. Covers ``1.2.3``, ``1.2.3rc1``, ``1.2.3-b.1``.
# ``\Z`` rather than ``$``: ``$`` also matches before a trailing newline, so
# ``$`` would accept a value carrying one — the single character that turns one
# shell argument into two lines of script.
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]*\Z")


def pending_release_exclusion(version: str) -> str:
    """Return a lychee ``--exclude`` regex for the compare link *targeting* ``version``.

    Only the target side is matched: ``vX.Y.Z...vNEXT`` names a different
    unknown and stays checked, as does any non-``/compare/`` URL naming the
    same version.
    """
    if not _VERSION_RE.match(version):
        raise ValueError(f"refusing to build a lychee exclusion from a non-version-shaped VERSION: {version!r}")
    return rf"^https://github\.com/[^/]+/[^/]+/compare/[^/]+\.\.\.v{re.escape(version)}$"


def main() -> int:
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    print(f"exclude={pending_release_exclusion(version)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
