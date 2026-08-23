# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from octowright.plugins.artifacts import ArtifactError, reserve_artifact
from octowright.recorder import Recorder


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def recording(tmp_path):
    log_path = tmp_path / "20260823T000000Z-refkind-refsess01.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    yield recorder, log_path, tmp_path
    recorder.close()


def _reserve(recording, artifact_id="transcript", suffix=".txt"):
    recorder, _log_path, root = recording
    return reserve_artifact(
        recorder=recorder,
        instance_id="refsess01",
        recordings_dir=root,
        artifact_id=artifact_id,
        suffix=suffix,
    )


def test_reserved_path_is_contained_and_its_directory_exists(recording):
    _, _, root = recording
    handle = _reserve(recording)
    assert handle.path.parent.exists(), "core must create the artifact dir before handing out the path"
    assert handle.path.resolve().is_relative_to(root.resolve())
    assert not handle.path.exists(), "reserve must not create the file itself"


def test_artifact_directory_is_owner_only(recording, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS_PRIVATE", "1")
    handle = _reserve(recording)
    assert stat.S_IMODE(handle.path.parent.stat().st_mode) == 0o700


def test_commit_writes_a_control_row_with_a_relative_path(recording):
    recorder, log_path, root = recording
    handle = _reserve(recording)
    handle.path.write_text("hello")
    handle.commit(mime_type="text/plain")
    recorder.close()

    row = [r for r in _rows(log_path) if r["action"] == "artifact_registered"][-1]
    assert row["artifact_id"] == "transcript"
    assert row["mime_type"] == "text/plain"
    assert not Path(row["path"]).is_absolute(), "path must be stored relative to the recordings root"
    assert (root / row["path"]).resolve() == handle.path.resolve()


def test_uncommitted_artifact_writes_no_row(recording):
    recorder, log_path, _ = recording
    handle = _reserve(recording)
    handle.path.write_text("orphan")
    recorder.close()
    assert [r for r in _rows(log_path) if r["action"] == "artifact_registered"] == []


def test_committing_the_same_id_twice_records_both(recording):
    recorder, log_path, _ = recording
    first = _reserve(recording)
    first.path.write_text("v1")
    first.commit(mime_type="text/plain")
    second = _reserve(recording)
    second.path.write_text("v2")
    second.commit(mime_type="text/plain")
    recorder.close()

    rows = [r for r in _rows(log_path) if r["action"] == "artifact_registered"]
    assert len(rows) == 2, "both commits are recorded; the reader takes the last"
    assert rows[-1]["artifact_id"] == "transcript"


def test_commit_rejects_a_mime_type_outside_the_allowlist(recording):
    handle = _reserve(recording)
    handle.path.write_text("x")
    with pytest.raises(ArtifactError, match="mime type"):
        handle.commit(mime_type="application/x-msdownload")


@pytest.mark.parametrize("artifact_id", ["../escape", "a/b", "/abs", "..", "", "with space", "UPPER"])
def test_bad_artifact_ids_are_refused(recording, artifact_id):
    with pytest.raises(ArtifactError):
        _reserve(recording, artifact_id=artifact_id)


@pytest.mark.parametrize("suffix", ["../x", "/x", "no-dot", ".a/b"])
def test_bad_suffixes_are_refused(recording, suffix):
    with pytest.raises(ArtifactError):
        _reserve(recording, suffix=suffix)


def test_a_symlinked_artifact_dir_cannot_escape(recording, tmp_path):
    _, _, root = recording
    outside = tmp_path.parent / "outside-root"
    outside.mkdir(exist_ok=True)
    # Pre-create the artifact dir as a symlink pointing out of the root; the
    # containment check must resolve it before deciding.
    art_dir = root / "session-artifacts" / "refsess01"
    art_dir.parent.mkdir(parents=True, exist_ok=True)
    art_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="resolves outside"):
        _reserve(recording)
