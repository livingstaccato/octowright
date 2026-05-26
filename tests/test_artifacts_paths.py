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
