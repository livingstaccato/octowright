# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for ``validate_upload_path``.

The validator is the single gate between an LLM-driven
``browser_set_input_files`` call (or a hand-edited macro replay) and the
host filesystem. Cover allowlist acceptance, symlink-escape rejection,
and parent-traversal rejection so a future refactor can't quietly widen
the surface.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from octowright import defaults
from octowright.session.upload_paths import validate_upload_path


@pytest.fixture(autouse=True)
def _isolate_upload_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the default upload root (the staging dir) at the test's
    tmp_path, and chdir into it too. Without this, the test inherits the
    operator's real staging dir and any extra OCTOWRIGHT_UPLOAD_ROOTS, which
    makes the symlink-escape assertion order-dependent across machines. The
    chdir is no longer load-bearing for the allowlist itself -- CWD is not
    a default upload root (see test_validate_rejects_cwd_only_path) -- but
    is kept so no test here accidentally depends on the real process CWD.
    """
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    monkeypatch.chdir(tmp_path)


def test_validate_accepts_path_inside_staging_dir(tmp_path: Path) -> None:
    target = tmp_path / "upload.txt"
    target.write_text("payload")
    resolved = validate_upload_path(str(target))
    assert resolved == target.resolve()


def test_validate_rejects_cwd_only_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: CWD alone must NOT grant upload access.

    Before the fix, ``_allowed_upload_roots`` unconditionally included
    ``Path.cwd()``, so any file under the daemon's working directory could
    be uploaded regardless of the staging-dir allowlist -- including a
    project's ``.env`` file or an SSH key that happened to live in the
    working tree an agent's page-driven `browser_set_input_files` call
    could name. A file that lives only under a fresh CWD -- not under the
    staging dir, not in OCTOWRIGHT_UPLOAD_ROOTS -- must now be rejected.
    """
    cwd_only_dir = tmp_path.parent / "octowright_cwd_only_test_dir"
    cwd_only_dir.mkdir(exist_ok=True)
    target = cwd_only_dir / "not_staged.txt"
    target.write_text("should not be uploadable")
    monkeypatch.chdir(cwd_only_dir)
    try:
        with pytest.raises(ValueError, match="outside the allowed roots"):
            validate_upload_path(str(target))
    finally:
        # Windows refuses to remove a directory that is the process's current
        # working directory (WinError 32) -- monkeypatch only restores the
        # original cwd at test teardown, after this block runs, so step out
        # of cwd_only_dir explicitly before deleting it.
        os.chdir(tmp_path)
        target.unlink()
        cwd_only_dir.rmdir()


def test_validate_accepts_extra_root_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    extra = tmp_path / "extra_root"
    extra.mkdir()
    target = extra / "upload.bin"
    target.write_bytes(b"\x00")
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", str(extra))
    assert validate_upload_path(str(target)) == target.resolve()


def test_validate_accepts_pathsep_joined_extra_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple extra roots come in os.pathsep-separated; each one extends
    the allowlist independently."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", os.pathsep.join([str(root_a), str(root_b)]))
    target = root_b / "upload.txt"
    target.write_text("y")
    assert validate_upload_path(str(target)) == target.resolve()


def test_validate_rejects_symlink_escape(tmp_path: Path) -> None:
    """A symlink inside the staging dir that points outside it must be
    rejected — .resolve() canonicalises through the link before the
    allowlist comparison runs."""
    outside_dir = tmp_path.parent / "octowright_outside_test_dir"
    outside_dir.mkdir(exist_ok=True)
    secret = outside_dir / "secret.txt"
    secret.write_text("nope")

    link = tmp_path / "leak"
    try:
        link.symlink_to(secret)
        with pytest.raises(ValueError, match="outside the allowed roots"):
            validate_upload_path(str(link))
    finally:
        if link.is_symlink() or link.exists():
            link.unlink()
        secret.unlink()
        outside_dir.rmdir()


def test_validate_rejects_parent_traversal(tmp_path: Path) -> None:
    """A path with ``..`` segments that escapes the staging dir must be
    rejected after resolution."""
    outside_dir = tmp_path.parent / "octowright_traversal_test_dir"
    outside_dir.mkdir(exist_ok=True)
    victim = outside_dir / "victim.txt"
    victim.write_text("data")
    traversal = tmp_path / ".." / outside_dir.name / "victim.txt"
    try:
        with pytest.raises(ValueError, match="outside the allowed roots"):
            validate_upload_path(str(traversal))
    finally:
        victim.unlink()
        outside_dir.rmdir()


def test_validate_rejects_nonexistent_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        validate_upload_path(str(tmp_path / "missing.txt"))


def test_validate_rejects_empty_string() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        validate_upload_path("")


def test_validate_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        validate_upload_path(None)  # type: ignore[arg-type]
