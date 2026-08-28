# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_defaults() -> None:
    yield

    import octowright.artifacts.paths as paths
    import octowright.defaults as defaults

    importlib.reload(defaults)
    importlib.reload(paths)


def _reload_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import octowright.defaults as defaults

    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "recordings"))
    importlib.reload(defaults)

    import octowright.artifacts.paths as paths

    importlib.reload(paths)
    return paths, defaults.RECORDINGS_DIR


def test_macro_artifact_dir_is_contained_and_slugged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)

    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    artifact_dir = store.macro_dir("Login Flow!!")

    assert artifact_dir == recordings_dir / "artifacts" / "macros" / "Login-Flow"
    assert artifact_dir.exists()


def test_macro_artifact_dir_rejects_path_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)

    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    with pytest.raises(ValueError, match="outside"):
        store._contained(Path("/tmp/outside"), label="bad path")


def test_macro_artifact_dir_rejects_symlinked_artifacts_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)
    recordings_dir.mkdir(parents=True)
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    (recordings_dir / "artifacts").symlink_to(outside, target_is_directory=True)

    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    with pytest.raises(ValueError, match="outside"):
        store.macro_dir("login")


def test_next_run_dir_increments(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)

    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    macro_dir = store.macro_dir("login")

    first = store.next_run_dir(macro_dir)
    second = store.next_run_dir(macro_dir)

    assert first.name == "run_0001"
    assert second.name == "run_0002"
    assert first.exists()
    assert second.exists()


def test_resolve_export_path_defaults_under_exports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)

    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    export_path = store.resolve_macro_export_path("login", None)

    assert export_path == recordings_dir / "artifacts" / "macros" / "login" / "exports" / "login.py"
    assert export_path.parent.exists()


def test_resolve_export_path_rejects_outside_recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)

    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    with pytest.raises(ValueError, match="outside"):
        store.resolve_macro_export_path("login", str(tmp_path / "elsewhere" / "login.py"))


def test_a_relative_export_path_is_anchored_under_the_artifact_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`if NOT target.is_absolute()` -- inverting it swaps both branches at once.

    A relative `out_path` would stop being anchored to the artifact root (and
    resolve against the process CWD instead), while an absolute one would be
    joined to the root. `reject_unsafe_path` still stands behind this, so the
    inversion is a containment weakening rather than an outright escape -- but
    the anchoring is the part that makes a relative export land somewhere
    predictable, and nothing asserted it.
    """
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)
    store = paths.ArtifactStore(recordings_dir=recordings_dir)

    target = store.resolve_macro_export_path("login", "subdir/login-cli.py")

    assert target == store.root / "subdir" / "login-cli.py"
    assert target.parent.exists()


def test_an_absolute_export_path_inside_the_root_is_used_as_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other branch, so the pair pins the condition rather than one side."""
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)
    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    wanted = store.root / "explicit" / "login-cli.py"

    assert store.resolve_macro_export_path("login", str(wanted)) == wanted


def test_a_stray_file_in_runs_does_not_stop_the_run_id_scan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`continue`, not `break` -- the scan must see every entry.

    `next_run_dir` walks `runs/` for the highest `run_NNNN` it can find, and
    skips anything that is not a directory. Swap that `continue` for `break`
    and the walk stops at the first stray file -- a `.DS_Store`, an editor
    swapfile, a leftover archive -- so the next run reuses an id that already
    exists and `mkdir(exist_ok=False)` raises, or worse, overwrites a bundle a
    verification still refers to. Directory iteration order is unspecified, so
    this is a latent failure that depends on the filesystem.
    """
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)
    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    artifact_dir = store.macro_dir("login")

    first = store.next_run_dir(artifact_dir)
    assert first.name == "run_0001"

    # A non-directory entry sitting among the run dirs.
    (artifact_dir / "runs" / ".DS_Store").write_text("", encoding="utf-8")

    second = store.next_run_dir(artifact_dir)
    assert second.name == "run_0002"
    assert second.exists()


def test_the_run_id_scan_ignores_names_that_are_not_run_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only `run_NNNN` counts toward the maximum."""
    paths, recordings_dir = _reload_paths(monkeypatch, tmp_path)
    store = paths.ArtifactStore(recordings_dir=recordings_dir)
    artifact_dir = store.macro_dir("login")
    runs = artifact_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "archive").mkdir()
    (runs / "run_9999_old").mkdir()

    assert store.next_run_dir(artifact_dir).name == "run_0001"
