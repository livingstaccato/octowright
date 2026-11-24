# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Interaction routes share the dashboard authentication and ID boundary."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from octowright.http.pairing import DASHBOARD_STATE_ATTR, DashboardPairingState
from tests.test_interactions_storage import row


def route_module() -> Any:
    try:
        return importlib.import_module("octowright.http.routes.interactions")
    except ModuleNotFoundError:
        pytest.fail("guarded interaction reader is not implemented")


def test_reader_pairing_host_and_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = route_module()
    from octowright.interaction_storage import InteractionSink

    log = tmp_path / "x.jsonl"
    sink = InteractionSink(log)
    sink.append(row(1))
    sink.append(row(2))
    sink.close()
    monkeypatch.setattr(module, "_resolve_log_path", lambda sid: log if sid == "abc" else None)
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "1")
    app = Starlette(routes=module.routes())
    pairing = DashboardPairingState(expected_token="test-token")
    setattr(app.state, DASHBOARD_STATE_ATTR, pairing)
    grant = pairing.redeem_code(pairing.mint_code())
    assert grant is not None
    auth = {"Authorization": f"Bearer {grant.bearer}"}
    with TestClient(app) as client:
        url = "/api/sessions/abc/interactions"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={**auth, "host": "attacker.example"}).status_code == 403
        first = client.get(url + "?limit=1", headers=auth).json()
        assert first["events"] == [row(1)] and not first["complete"]
        second = client.get(url + f"?cursor={first['next_cursor']}", headers=auth).json()
        assert second["events"] == [row(2)] and second["complete"]
        for query in ("cursor=-1", "cursor=no", "limit=5001", "limit=0"):
            assert client.get(url + "?" + query, headers=auth).status_code == 400
        assert client.get("/api/sessions/a-b/interactions", headers=auth).status_code == 404
        assert client.get("/api/sessions/missing/interactions", headers=auth).status_code == 404
        log.with_suffix(".interactions.jsonl").unlink()
        assert client.get(url, headers=auth).json()["available"] is False


def test_sidecar_discovery_live_preservation_and_deletion(tmp_path: Path) -> None:
    from octowright.http import discovery
    from octowright.http.recording_sidecars import is_session_recording
    from octowright.http.routes.sessions_recording import _remove_session_artifacts
    from octowright.recording_cleanup import _classify, session_file_matcher

    log = tmp_path / "20260101T000000Z-chromium-abc.jsonl"
    log.write_text('{"action":"launch","kind":"chromium"}\n')
    sidecar = log.with_suffix(".interactions.jsonl")
    sidecar.write_text("{}\n")
    neighbor = tmp_path / f"{log.stem}def.interactions.jsonl"
    neighbor.write_text("{}\n")
    assert not is_session_recording(sidecar.name.upper())
    assert discovery._find_recording_for("abc", tmp_path) == log
    assert len(discovery._summaries_for(tmp_path)) == 1
    assert _classify(sidecar, tmp_path) == "other"
    matcher = session_file_matcher([SimpleNamespace(log_path=log, instance_id="abc")])
    assert matcher(sidecar) and not matcher(neighbor)
    _remove_session_artifacts("abc", log, tmp_path)
    assert not sidecar.exists() and neighbor.exists()
