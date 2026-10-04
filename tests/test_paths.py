# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the central path-containment helper.

``reject_unsafe_path`` and ``safe_under`` are the single audited check
between any untrusted (LLM- or operator-supplied) name and a filesystem
operation. Cover every branch so a future change can't quietly weaken the
guard.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from octowright._paths import reject_unsafe_path, safe_under


def test_safe_under_returns_true_for_inside_path(tmp_path: Path) -> None:
    inside = tmp_path / "child" / "file.txt"
    assert safe_under(inside, tmp_path) is True


def test_safe_under_returns_true_for_root_itself(tmp_path: Path) -> None:
    """A candidate that resolves to root is conventionally inside root."""
    assert safe_under(tmp_path, tmp_path) is True


def test_safe_under_returns_false_for_escaping_path(tmp_path: Path) -> None:
    outside = tmp_path.parent / "sibling" / "file.txt"
    assert safe_under(outside, tmp_path) is False


def test_safe_under_handles_resolve_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If ``.resolve()`` raises (e.g. a too-long path), treat as unsafe."""
    real_resolve = Path.resolve

    def broken_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        raise OSError("simulated resolve failure")

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    try:
        assert safe_under(tmp_path / "x", tmp_path) is False
    finally:
        monkeypatch.setattr(Path, "resolve", real_resolve)


def test_reject_unsafe_path_returns_resolved_candidate(tmp_path: Path) -> None:
    inside = tmp_path / "child" / "file.txt"
    resolved = reject_unsafe_path(inside, tmp_path, label="x")
    assert resolved == inside.resolve()


def test_reject_unsafe_path_raises_for_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "sibling" / "file.txt"
    with pytest.raises(ValueError, match="resolves outside"):
        reject_unsafe_path(outside, tmp_path, label="screenshot path")


def test_reject_unsafe_path_includes_label_in_message(tmp_path: Path) -> None:
    outside = tmp_path.parent / "evil"
    with pytest.raises(ValueError, match="custom-label"):
        reject_unsafe_path(outside, tmp_path, label="custom-label")


# ---------------------------------------------------------------------------
# atomic_write_via_writer + symlink-swap defence
#
# The atomic helper exists specifically to close the TOCTOU window between
# the caller's containment check and the writer's open(). Cover both the
# happy path (overwrite an existing target) and the adversarial cases
# (symlinked parent, symlinked target pointing outside the safe dir).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_atomic_write_via_writer_overwrites_existing_target(tmp_path: Path) -> None:
    """Overwriting an existing file via the atomic helper must replace its
    contents fully (os.replace is destructive-by-design) and leave no stray
    temp siblings in the parent dir."""
    from octowright._paths import atomic_write_via_writer

    target = tmp_path / "data.txt"
    target.write_text("old")

    async def writer(tmp: Path) -> None:
        tmp.write_text("new")

    await atomic_write_via_writer(target, writer)

    assert target.read_text() == "new"
    leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".data.txt.")]
    assert leftover == []


@pytest.mark.anyio
async def test_atomic_write_via_writer_cleans_up_temp_on_writer_failure(tmp_path: Path) -> None:
    """If the writer raises, the temp sibling must be unlinked so a poisoned
    write can't accumulate orphan dotfiles in the recordings root."""
    from octowright._paths import atomic_write_via_writer

    target = tmp_path / "data.txt"
    target.write_text("keep")

    async def writer(tmp: Path) -> None:
        raise RuntimeError("simulated writer failure")

    with pytest.raises(RuntimeError, match="simulated"):
        await atomic_write_via_writer(target, writer)

    assert target.read_text() == "keep"
    leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".data.txt.")]
    assert leftover == []


@pytest.mark.anyio
async def test_atomic_write_via_writer_through_symlinked_parent(tmp_path: Path) -> None:
    """When the parent dir is reached via a symlink, the write still lands in
    the real underlying directory. The atomic helper resolves the parent via
    `path.parent` at temp-file creation time, so the temp sibling and the
    final rename both target the same physical directory."""
    from octowright._paths import atomic_write_via_writer

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir)

    target = link_dir / "data.txt"

    async def writer(tmp: Path) -> None:
        tmp.write_text("via-symlink")

    await atomic_write_via_writer(target, writer)
    assert (real_dir / "data.txt").read_text() == "via-symlink"


@pytest.mark.anyio
async def test_atomic_write_via_writer_target_is_symlink_outside_safe_dir(tmp_path: Path) -> None:
    """If the caller hands the helper a target that's already a symlink to
    somewhere outside the safe dir, `os.replace` overwrites the symlink itself
    (replaces the link, does NOT follow it). This pins that defensive behaviour
    so a future refactor doesn't accidentally introduce target-following.
    """
    from octowright._paths import atomic_write_via_writer

    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_target = outside_dir / "victim.txt"
    outside_target.write_text("untouched")

    target = safe_dir / "data.txt"
    target.symlink_to(outside_target)

    async def writer(tmp: Path) -> None:
        tmp.write_text("safe-payload")

    await atomic_write_via_writer(target, writer)

    assert outside_target.read_text() == "untouched"
    assert target.read_text() == "safe-payload"
    assert not target.is_symlink()


# ---------------------------------------------------------------------------
# atomic write helpers preserve the target's existing mode
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="Windows uses ACLs/read-only attributes, not POSIX mode bits")
def test_atomic_write_text_preserves_existing_target_mode(tmp_path: Path) -> None:
    from octowright._paths import atomic_write_text

    target = tmp_path / "perm.txt"
    target.write_text("old")
    os.chmod(target, 0o644)

    atomic_write_text(target, "new")

    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="Windows uses ACLs/read-only attributes, not POSIX mode bits")
def test_atomic_write_text_leaves_new_file_at_tempfile_default(tmp_path: Path) -> None:
    from octowright._paths import atomic_write_text

    target = tmp_path / "fresh.txt"
    atomic_write_text(target, "hello")

    assert target.read_text() == "hello"
    # No prior file → no mode to inherit; NamedTemporaryFile's 0o600 default
    # carries through. Documenting the behaviour so a future change is intentional.
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Windows uses ACLs/read-only attributes, not POSIX mode bits")
async def test_atomic_write_via_writer_preserves_existing_target_mode(tmp_path: Path) -> None:
    from octowright._paths import atomic_write_via_writer

    target = tmp_path / "perm.txt"
    target.write_text("old")
    os.chmod(target, 0o640)

    async def writer(tmp: Path) -> None:
        tmp.write_text("new")

    await atomic_write_via_writer(target, writer)

    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


# ---------------------------------------------------------------------------
# The parent directory is pinned: a swap after validation cannot redirect it
# ---------------------------------------------------------------------------

_POSIX_DIR_FD = pytest.mark.skipif(os.name == "nt", reason="directory descriptors are POSIX-only")


def _swap_for_symlink(directory: Path, to: Path) -> None:
    """Replace ``directory`` with a symlink to ``to``, the attacker's move."""
    directory.rename(directory.with_name(directory.name + ".moved"))
    directory.symlink_to(to, target_is_directory=True)


@_POSIX_DIR_FD
def test_atomic_write_text_refuses_a_parent_swapped_for_a_symlink_after_validation(tmp_path: Path) -> None:
    from octowright._paths import atomic_write_text
    from octowright.request_errors import InvalidRequestError

    root = tmp_path / "recordings"
    (root / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "out.py").write_text("KEEP")
    target = reject_unsafe_path(root / "sub" / "out.py", root, label="out_path")
    _swap_for_symlink(root / "sub", outside)

    with pytest.raises(InvalidRequestError, match="not a plain directory"):
        atomic_write_text(target, "payload", root=root)

    assert (outside / "out.py").read_text() == "KEEP"
    assert sorted(p.name for p in outside.iterdir()) == ["out.py"]


@_POSIX_DIR_FD
@pytest.mark.anyio
async def test_atomic_write_via_writer_never_renames_into_a_parent_swapped_mid_write(tmp_path: Path) -> None:
    """The writer (Playwright, a copy) can only be handed a path, so a parent
    swapped while it runs sends ITS bytes elsewhere; the final rename must
    still not replace a file outside the directory the write started in."""
    from octowright._paths import atomic_write_via_writer
    from octowright.request_errors import InvalidRequestError

    real = tmp_path / "real"
    real.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "shot.png").write_text("KEEP")

    async def writer(tmp: Path) -> None:
        _swap_for_symlink(real, outside)
        tmp.write_text("payload")

    with pytest.raises(InvalidRequestError, match="changed during the write"):
        await atomic_write_via_writer(real / "shot.png", writer)

    assert (outside / "shot.png").read_text() == "KEEP"
