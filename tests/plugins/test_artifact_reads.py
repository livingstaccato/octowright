# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

from octowright.plugins.artifacts import read_registered_artifacts, reserve_artifact
from octowright.recorder import Recorder


def _recording(tmp_path: Path) -> tuple[Recorder, Path]:
    log_path = tmp_path / "20260823T000000Z-refkind-refsess01.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    return recorder, log_path


def _commit(recorder: Recorder, root: Path, body: str) -> Path:
    handle = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=root, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text(body)
    handle.commit(mime_type="text/plain")
    return handle.path


def test_committed_artifact_reads_back_as_an_absolute_contained_path(tmp_path):
    recorder, log_path = _recording(tmp_path)
    _commit(recorder, tmp_path, "hello")
    recorder.close()

    found = read_registered_artifacts(log_path, tmp_path)
    assert [a.artifact_id for a in found] == ["transcript"]
    assert found[0].path.is_absolute()
    assert found[0].path.read_text() == "hello"
    assert found[0].mime_type == "text/plain"


def test_uncommitted_artifact_is_invisible(tmp_path):
    recorder, log_path = _recording(tmp_path)
    handle = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=tmp_path, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text("orphan")
    recorder.close()
    assert read_registered_artifacts(log_path, tmp_path) == []


def test_last_commit_of_an_id_wins(tmp_path):
    recorder, log_path = _recording(tmp_path)
    _commit(recorder, tmp_path, "v1")
    _commit(recorder, tmp_path, "v2")
    recorder.close()

    found = read_registered_artifacts(log_path, tmp_path)
    assert len(found) == 1
    assert found[0].path.read_text() == "v2"


def test_a_row_pointing_outside_the_root_is_dropped(tmp_path):
    recorder, log_path = _recording(tmp_path)
    recorder.record_control(
        "artifact_registered", artifact_id="evil", path="../../../../etc/passwd", mime_type="text/plain"
    )
    recorder.close()
    assert read_registered_artifacts(log_path, tmp_path) == []


def test_a_row_with_a_disallowed_mime_type_is_dropped(tmp_path):
    recorder, log_path = _recording(tmp_path)
    path = _commit(recorder, tmp_path, "x")
    # Bypass commit's own allowlist check to simulate a hand-edited recording.
    recorder.record_control(
        "artifact_registered",
        artifact_id="transcript",
        path=str(path.relative_to(tmp_path.resolve())),
        mime_type="application/x-msdownload",
    )
    recorder.close()
    assert read_registered_artifacts(log_path, tmp_path) == []


def test_a_missing_file_is_dropped(tmp_path):
    recorder, log_path = _recording(tmp_path)
    recorder.record_control(
        "artifact_registered", artifact_id="gone", path="session-artifacts/refsess01/gone.txt", mime_type="text/plain"
    )
    recorder.close()
    assert read_registered_artifacts(log_path, tmp_path) == []


def test_an_unparsable_line_does_not_break_the_scan(tmp_path):
    recorder, log_path = _recording(tmp_path)
    _commit(recorder, tmp_path, "hello")
    recorder.close()
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    assert [a.artifact_id for a in read_registered_artifacts(log_path, tmp_path)] == ["transcript"]


def test_a_recording_that_does_not_exist_yields_nothing(tmp_path):
    assert read_registered_artifacts(tmp_path / "absent.jsonl", tmp_path) == []
