# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The artifact store's directory layout and its containment errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.artifacts.paths import ArtifactStore, slug


def _store(tmp_path: Path) -> tuple[ArtifactStore, Path]:
    recordings = (tmp_path / "recordings").resolve()
    recordings.mkdir()
    return ArtifactStore(recordings_dir=recordings), recordings


def _outside(tmp_path: Path) -> Path:
    outside = (tmp_path / "elsewhere").resolve()
    outside.mkdir()
    return outside


@pytest.mark.parametrize(
    ("value", "expected"),
    [("MAX", "MAX"), (" my macro! ", "my-macro"), ("..hidden..", "hidden"), ("-X-", "X"), ("v1.2_x", "v1.2_x")],
)
def test_slug(value: str, expected: str) -> None:
    assert slug(value) == expected


def test_an_empty_slug_is_refused_by_name() -> None:
    with pytest.raises(ValueError) as excinfo:
        slug(" -.- ")

    assert str(excinfo.value) == "artifact name ' -.- ' produced an empty slug"


def test_a_macro_dir_escaping_through_a_symlink_names_the_macro(tmp_path: Path) -> None:
    store, recordings = _store(tmp_path)
    outside = _outside(tmp_path)
    (recordings / "artifacts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError) as excinfo:
        store.macro_dir("login")

    assert str(excinfo.value) == (
        f"macro artifact 'login' {str(recordings / 'artifacts' / 'macros' / 'login')!r} "
        f"resolves outside {str(recordings)!r}"
    )


def test_a_runs_dir_outside_the_recordings_names_itself(tmp_path: Path) -> None:
    store, recordings = _store(tmp_path)
    outside = _outside(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        store.next_run_dir(outside)

    assert str(excinfo.value) == f"artifact runs dir {str(outside / 'runs')!r} resolves outside {str(recordings)!r}"


def test_an_existing_run_outside_the_recordings_names_the_run(tmp_path: Path) -> None:
    store, recordings = _store(tmp_path)
    outside = _outside(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        store.existing_run_dir(outside, "run_0001")

    assert str(excinfo.value) == (
        f"artifact run 'run_0001' {str(outside / 'runs' / 'run_0001')!r} resolves outside {str(recordings)!r}"
    )


def test_next_run_dir_creates_the_artifact_dir_chain(tmp_path: Path) -> None:
    store, recordings = _store(tmp_path)
    artifact_dir = recordings / "artifacts" / "macros" / "fresh"

    first = store.next_run_dir(artifact_dir)
    second = store.next_run_dir(artifact_dir)

    assert first == artifact_dir / "runs" / "run_0001"
    assert second == artifact_dir / "runs" / "run_0002"
    assert first.is_dir()
    assert second.is_dir()


def test_next_run_dir_never_reuses_a_run_claimed_after_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent writer creating the next run between the scan and the claim must fail the claim."""
    store, recordings = _store(tmp_path)
    artifact_dir = recordings / "artifacts" / "macros" / "race"
    runs = artifact_dir / "runs"
    (runs / "run_0001").mkdir(parents=True)
    real_iterdir = Path.iterdir

    def stale_scan(self: Path):
        # The listing as it stood before the other writer created run_0001.
        if self == runs:
            return iter(())
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", stale_scan)

    with pytest.raises(FileExistsError):
        store.next_run_dir(artifact_dir)


def test_the_default_export_path_is_stable_across_calls(tmp_path: Path) -> None:
    store, recordings = _store(tmp_path)

    first = store.resolve_macro_export_path("My Macro", None)
    second = store.resolve_macro_export_path("My Macro", None)

    assert first == recordings / "artifacts" / "macros" / "My-Macro" / "exports" / "My-Macro.py"
    assert second == first
    assert first.parent.is_dir()
