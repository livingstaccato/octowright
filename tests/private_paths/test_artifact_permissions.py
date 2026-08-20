# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Capture/golden/macro roots must not be readable by other local users.

These hold the same class of data the JSONL recording does -- page text,
accessibility trees, evaluate results, and (since request headers became
recordable) Authorization/Cookie values in a ``OCTOWRIGHT_REDACT_INPUTS=off``
deployment. Recordings are already 0600 inside an 0700 parent; these three
roots were 0755, so the protection was inconsistent rather than absent.

The DIRECTORY is the control, not the file mode, and that is not a stylistic
preference: ``atomic_write_text`` deliberately preserves an existing target's
mode (an atomic write must be a content replacement, not a permission change),
so a file first written at 0644 before the atomic-write change keeps 0644
through every later rewrite, forever. A 0700 directory denies traversal and
covers every file inside it regardless of age or mode.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from octowright.private_paths import (
    RECORDINGS_PRIVATE_ENV,
    recordings_private,
    secure_artifact_tree,
)

# Windows ignores POSIX mode bits on directories, so the assertions below say
# nothing there. The production helper is best-effort and simply no-ops.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission-bit assertion")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    """A captures-shaped tree: root / host / session."""
    root = tmp_path / "captures"
    leaf = root / "app.example.test" / "a1afbc5331ca"
    leaf.mkdir(parents=True)
    for directory in (root, root / "app.example.test", leaf):
        directory.chmod(0o755)
    return leaf, root


class TestPolicyResolution:
    def test_the_policy_is_on_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(RECORDINGS_PRIVATE_ENV, raising=False)

        assert recordings_private() is True

    def test_an_empty_value_still_means_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Matches the recorder: only an explicit falsey token disables a
        security default, so an accidentally-blank export cannot silently
        widen permissions."""
        monkeypatch.setenv(RECORDINGS_PRIVATE_ENV, "")

        assert recordings_private() is True

    @pytest.mark.parametrize("token", ["0", "off", "false", "no", "never", "none", "disabled"])
    def test_falsey_tokens_opt_out(self, monkeypatch: pytest.MonkeyPatch, token: str) -> None:
        monkeypatch.setenv(RECORDINGS_PRIVATE_ENV, token)

        assert recordings_private() is False


class TestSecureArtifactTree:
    def test_every_directory_up_to_the_root_is_locked(
        self, tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreadable leaf inside a world-readable parent still leaks the
        host names and session ids the capture paths are built from."""
        monkeypatch.delenv(RECORDINGS_PRIVATE_ENV, raising=False)
        leaf, root = tree

        secure_artifact_tree(leaf, root)

        assert _mode(leaf) == 0o700
        assert _mode(leaf.parent) == 0o700
        assert _mode(root) == 0o700

    def test_a_flat_root_is_locked_when_leaf_is_the_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Goldens and macros are flat -- the leaf IS the root."""
        monkeypatch.delenv(RECORDINGS_PRIVATE_ENV, raising=False)
        root = tmp_path / "goldens"
        root.mkdir()
        root.chmod(0o755)

        secure_artifact_tree(root, root)

        assert _mode(root) == 0o700

    def test_opting_out_leaves_the_umask_in_place(
        self, tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(RECORDINGS_PRIVATE_ENV, "off")
        leaf, root = tree

        secure_artifact_tree(leaf, root)

        assert _mode(root) == 0o755

    def test_a_leaf_outside_the_root_does_not_walk_to_the_filesystem_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator pointing OCTOWRIGHT_CAPTURES_DIR somewhere unexpected must
        not chmod unrelated parents to 0700 on the way up."""
        monkeypatch.delenv(RECORDINGS_PRIVATE_ENV, raising=False)
        root = tmp_path / "captures"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "leaf"
        outside.mkdir(parents=True)
        outside.chmod(0o755)
        outside.parent.chmod(0o755)

        secure_artifact_tree(outside, root)

        assert _mode(outside) == 0o700
        # The walk stopped here. (pytest's own tmp_path is already 0700, so it
        # cannot serve as the negative -- this explicitly-0755 parent can.)
        assert _mode(outside.parent) == 0o755

    def test_a_missing_directory_never_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Best-effort by design: a chmod that cannot run must never break a
        capture, a golden save, or a macro write."""
        monkeypatch.delenv(RECORDINGS_PRIVATE_ENV, raising=False)

        secure_artifact_tree(tmp_path / "gone", tmp_path / "gone")
