# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Persona/profile directories must not be readable by other local users.

A profile dir holds live session cookies for every site the persona has
logged into -- a strictly stronger credential than the typed password the
recorder already writes at 0600. Firefox and WebKit write ``cookies.sqlite``
at 0644 into an 0755 tree, so the directory mode is the control.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from octowright.private_paths import PROFILES_PRIVATE_ENV, profiles_private, secure_profile_tree

# Windows ignores POSIX mode bits on directories, so the assertions below say
# nothing there. The production helper is best-effort and simply no-ops.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission-bit assertion")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "profiles"
    leaf = root / "tanuki-tim" / "firefox"
    leaf.mkdir(parents=True)
    for d in (root, root / "tanuki-tim", leaf):
        d.chmod(0o755)
    return leaf, root


def test_leaf_and_ancestors_become_owner_only(tree: tuple[Path, Path]) -> None:
    leaf, root = tree
    secure_profile_tree(leaf, root)
    assert _mode(leaf) == 0o700
    assert _mode(leaf.parent) == 0o700
    assert _mode(root) == 0o700


def test_group_and_other_cannot_traverse(tree: tuple[Path, Path]) -> None:
    """The concrete property: no read or execute bit for group/other."""
    leaf, root = tree
    secure_profile_tree(leaf, root)
    forbidden = stat.S_IRWXG | stat.S_IRWXO
    assert _mode(leaf) & forbidden == 0
    assert _mode(root) & forbidden == 0


def test_walk_stops_at_the_profiles_root(tree: tuple[Path, Path]) -> None:
    """Nothing above the root is touched -- that is the user's own config tree."""
    leaf, root = tree
    above = root.parent
    above.chmod(0o755)
    secure_profile_tree(leaf, root)
    assert _mode(above) == 0o755


def test_leaf_outside_the_root_does_not_walk_up(tmp_path: Path) -> None:
    """Guards the runaway chmod: an uncontained leaf must not climb to /.

    Without the containment check the loop would keep calling parent until it
    hit the filesystem root, locking unrelated directories on the way.
    """
    root = tmp_path / "profiles"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "deep"
    outside.mkdir(parents=True)
    outside.parent.chmod(0o755)
    tmp_path.chmod(0o755)

    secure_profile_tree(outside, root)

    assert _mode(outside) == 0o700  # the leaf itself is still protected
    assert _mode(outside.parent) == 0o755  # but the walk stopped immediately
    assert _mode(tmp_path) == 0o755


@pytest.mark.parametrize("token", ["0", "off", "false", "no", "never", "none", "disabled"])
def test_opt_out_leaves_permissions_alone(monkeypatch: pytest.MonkeyPatch, tree: tuple[Path, Path], token: str) -> None:
    monkeypatch.setenv(PROFILES_PRIVATE_ENV, token)
    leaf, root = tree
    secure_profile_tree(leaf, root)
    assert _mode(leaf) == 0o755


def test_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROFILES_PRIVATE_ENV, raising=False)
    assert profiles_private() is True


def test_unreadable_directory_does_not_raise(tmp_path: Path) -> None:
    """Best-effort: a chmod failure must never block a browser launch."""
    root = tmp_path / "profiles"
    missing = root / "gone" / "firefox"
    secure_profile_tree(missing, root)  # never created; must not raise
