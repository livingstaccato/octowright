# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""DELETE /api/sessions/{id}/recording removes every artefact the session wrote.

It removed only recordings-dir files whose name starts with the JSONL stem, and
answered ``deleted: True`` while the video (``videos/<stem>/``), downloads
(``downloads/<id>/``), dashboard frame cache (``.frame-cache/<id>/``) and the
failure dumps (``<id>-fail-*.html/.png``) stayed on disk -- the parts most
likely to hold what the person deleting it wanted gone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from octowright import http as _http
from octowright.http import state as _http_state
from octowright.server import _state

SID = "delart000001"
OTHER = "delart000002"


class _FakePool:
    def maybe_get(self, instance_id: str) -> Any | None:
        return None

    def has_session(self, instance_id: str) -> bool:
        return False

    def iter_sessions(self) -> tuple[Any, ...]:
        return ()


class _FakeScenarioPool:
    def has_live(self, scenario_id: str) -> bool:
        return False

    def list_live(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "recordings"
    root.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", root)
    monkeypatch.setattr(_state, "pool", _FakePool())
    monkeypatch.setattr(_state, "scenario_pool", _FakeScenarioPool())
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    return root


@pytest.fixture
def client(rec: Path) -> TestClient:
    return TestClient(_http.build_app())


def _session_files(root: Path, sid: str) -> dict[str, Path]:
    stem = f"20260101T000000Z-chromium-{sid}"
    jsonl = root / f"{stem}.jsonl"
    jsonl.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    files = {
        "jsonl": jsonl,
        "video": root / "videos" / stem / "abc.webm",
        "download": root / "downloads" / sid / "000-report.pdf",
        "frame": root / ".frame-cache" / sid / "1.000.png",
        "fail_png": root / f"{sid}-fail-20260101T000000Z.png",
        "fail_html": root / f"{sid}-fail-20260101T000000Z.html",
    }
    for key, path in files.items():
        if key != "jsonl":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    return files


def test_every_artefact_of_the_session_is_removed_and_reported(client: TestClient, rec: Path) -> None:
    files = _session_files(rec, SID)

    response = client.delete(f"/api/sessions/{SID}/recording")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    for path in files.values():
        assert not path.exists(), path
    assert not (rec / "videos" / files["jsonl"].stem).exists()
    assert not (rec / "downloads" / SID).exists()
    assert not (rec / ".frame-cache" / SID).exists()
    assert sorted(body["removed"]) == sorted(
        [
            files["jsonl"].name,
            files["fail_png"].name,
            files["fail_html"].name,
            f"videos/{files['jsonl'].stem}",
            f"downloads/{SID}",
            f".frame-cache/{SID}",
        ]
    )
    assert body["files_removed"] == 3
    assert body["dirs_removed"] == 3


def test_another_sessions_artefacts_survive(client: TestClient, rec: Path) -> None:
    _session_files(rec, SID)
    other = _session_files(rec, OTHER)

    assert client.delete(f"/api/sessions/{SID}/recording").status_code == 200

    for path in other.values():
        assert path.exists(), path


def test_a_symlinked_artefact_dir_is_unlinked_not_followed(client: TestClient, rec: Path, tmp_path: Path) -> None:
    """Containment: a same-user symlink planted as downloads/<id> must not turn
    the delete into an rmtree of whatever it points at."""
    stem = f"20260101T000000Z-chromium-{SID}"
    (rec / f"{stem}.jsonl").write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (rec / "downloads").mkdir()
    (rec / "downloads" / SID).symlink_to(outside, target_is_directory=True)

    assert client.delete(f"/api/sessions/{SID}/recording").status_code == 200

    assert (outside / "keep.txt").exists()
    assert not (rec / "downloads" / SID).is_symlink()


def test_a_session_with_only_a_jsonl_still_deletes(client: TestClient, rec: Path) -> None:
    stem = f"20260101T000000Z-chromium-{SID}"
    (rec / f"{stem}.jsonl").write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")

    body = client.delete(f"/api/sessions/{SID}/recording").json()

    assert body["removed"] == [f"{stem}.jsonl"] and body["dirs_removed"] == 0
