# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
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


def test_many_rows_still_resolve_correctly_after_streaming(tmp_path):
    """Pins that switching from ``read_text().splitlines()`` to a streaming
    per-line read (final-fixes finding 2) does not change what is found, even
    across a JSONL file large enough that materializing it twice would matter.
    """
    recorder, log_path = _recording(tmp_path)
    _commit(recorder, tmp_path, "hello")
    # Pad the file with a large number of unrelated rows so it is genuinely
    # multi-KB -- the case the streaming rewrite targets.
    with log_path.open("a", encoding="utf-8") as fh:
        for i in range(5000):
            fh.write(json.dumps({"ts": "x", "action": "console", "text": f"noise-{i}"}) + "\n")
    recorder.close()

    found = read_registered_artifacts(log_path, tmp_path)
    assert [a.artifact_id for a in found] == ["transcript"]
    assert found[0].path.read_text() == "hello"


def test_invalid_utf8_line_does_not_hide_valid_rows_around_it(tmp_path):
    """A torn write mid multi-byte sequence must drop only that one line.

    ``read_text().splitlines()`` decodes the whole file up front, so one bad
    byte range would raise and hide every row -- including the good commits on
    either side of it.
    """
    recorder, log_path = _recording(tmp_path)
    handle1 = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=tmp_path, artifact_id="before", suffix=".txt"
    )
    handle1.path.write_text("before-body")
    handle1.commit(mime_type="text/plain")

    handle2 = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=tmp_path, artifact_id="after", suffix=".txt"
    )
    handle2.path.write_text("after-body")
    handle2.commit(mime_type="text/plain")
    recorder.close()

    # Splice a line with an invalid UTF-8 byte sequence between the two commit
    # rows.
    lines = log_path.read_bytes().splitlines(keepends=True)
    lines.insert(-1, b'{"action": "console", "text": "broken \xff\xfe"}\n')
    log_path.write_bytes(b"".join(lines))

    found = {a.artifact_id: a for a in read_registered_artifacts(log_path, tmp_path)}
    assert set(found) == {"before", "after"}
    assert found["before"].path.read_text() == "before-body"
    assert found["after"].path.read_text() == "after-body"


def test_a_hostile_artifact_id_row_is_dropped(tmp_path):
    """``artifact_id`` is the one row field ``_registered_from_row`` used to
    trust outright while the path and mime type were both re-validated. A
    hand-edited row with a path-like id must not survive the read, since it
    flows into the session-detail payload (and, in step 4, the DOM).
    """
    recorder, log_path = _recording(tmp_path)
    victim_dir = tmp_path / "session-artifacts" / "refsess01"
    victim_dir.mkdir(parents=True, exist_ok=True)
    (victim_dir / "real.txt").write_text("x")
    recorder.record_control(
        "artifact_registered",
        artifact_id="../../etc/passwd",
        path="session-artifacts/refsess01/real.txt",
        mime_type="text/plain",
    )
    recorder.close()

    assert read_registered_artifacts(log_path, tmp_path) == []
