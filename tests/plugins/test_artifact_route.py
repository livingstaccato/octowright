# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.plugins.artifacts import reserve_artifact
from octowright.recorder import Recorder


def _session_with_artifact(root: Path, sid: str = "sessionzz01") -> Path:
    log_path = root / f"20260823T000000Z-refkind-{sid}.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    handle = reserve_artifact(
        recorder=recorder, instance_id=sid, recordings_dir=root, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text("recorded output")
    handle.commit(mime_type="text/plain")
    recorder.close()
    return log_path


@pytest.fixture
def client_with_recordings(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from octowright.http import state as http_state
    from octowright.http.app import build_app

    monkeypatch.setattr(http_state, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
    return TestClient(build_app()), tmp_path


def test_committed_artifact_is_served(client_with_recordings):
    client, root = client_with_recordings
    _session_with_artifact(root)
    resp = client.get("/api/sessions/sessionzz01/artifacts/transcript")
    assert resp.status_code == 200
    assert resp.text == "recorded output"
    assert resp.headers["content-type"].startswith("text/plain")


def test_artifact_response_is_attachment_disposition(client_with_recordings):
    """The mime-type allowlist alone does not neutralize an allowlisted
    ``image/svg+xml`` artifact -- SVG is active content. What actually stops
    it executing in the dashboard's own origin (where the pairing bearer
    lives) is ``FileResponse``'s default ``content_disposition_type="attachment"``,
    which the artifact route relies on and must never override to ``inline``.
    This pins that the served response actually carries it.
    """
    client, root = client_with_recordings
    _session_with_artifact(root)
    resp = client.get("/api/sessions/sessionzz01/artifacts/transcript")
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment"), disposition


def test_unknown_artifact_is_404(client_with_recordings):
    client, root = client_with_recordings
    _session_with_artifact(root)
    assert client.get("/api/sessions/sessionzz01/artifacts/nosuch").status_code == 404


def test_unknown_session_is_404(client_with_recordings):
    client, _ = client_with_recordings
    assert client.get("/api/sessions/zzz999zzz999/artifacts/transcript").status_code == 404


def test_a_traversing_artifact_id_is_refused(client_with_recordings):
    client, root = client_with_recordings
    _session_with_artifact(root)
    resp = client.get("/api/sessions/sessionzz01/artifacts/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


def test_a_row_pointing_outside_the_root_is_not_served(client_with_recordings, tmp_path):
    client, root = client_with_recordings
    log_path = root / "20260823T000000Z-refkind-sessionzz02.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    secret = tmp_path.parent / "secret-not-served.txt"
    secret.write_text("do not serve me")
    recorder.record_control("artifact_registered", artifact_id="leak", path=f"../{secret.name}", mime_type="text/plain")
    recorder.close()

    assert client.get("/api/sessions/sessionzz02/artifacts/leak").status_code == 404


def test_a_hyphenated_session_id_is_refused_by_the_route(client_with_recordings):
    """IDOR regression, now closed at the source rather than at the lookup.

    The original defect: a naive ``f"-{sid}" in stem`` check matched a hyphenated
    session's recording for a SHORTER sid too -- ``foo-bar``'s file
    ``...-refkind-foo-bar.jsonl`` ends in ``-bar``, so a request for ``bar``
    received ``foo-bar``'s committed artifact even though ``bar`` never existed.

    That is unreachable twice over now. ``INSTANCE_ID_RE`` forbids a hyphen where
    core composes the filename, so no such recording can be written; and
    ``_valid_session_id`` forbids one in a request, so an id that could only name
    such a recording is refused outright instead of resolving to a prefix.
    """
    client, _root = client_with_recordings
    resp = client.get("/api/sessions/foo-bar/artifacts/transcript")
    assert resp.status_code == 400
    assert resp.text != "recorded output"


def test_a_hyphenated_instance_id_cannot_reserve_an_artifact(tmp_path):
    """The write side refuses it too, so no such artifact directory is created."""
    from octowright.plugins.artifacts import ArtifactError

    log_path = tmp_path / "20260823T000000Z-refkind-foobar.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    try:
        with pytest.raises(ArtifactError, match="must match"):
            reserve_artifact(
                recorder=recorder,
                instance_id="foo-bar",
                recordings_dir=tmp_path,
                artifact_id="transcript",
                suffix=".txt",
            )
    finally:
        recorder.close()
    assert not (tmp_path / "session-artifacts").exists()
