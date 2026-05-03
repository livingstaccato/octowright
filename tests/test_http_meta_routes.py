# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from octowright import http as _http
from octowright.http.routes import meta as _meta_routes
from octowright.server import _state


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    fake_pool = SimpleNamespace(_sessions={})
    fake_spool = SimpleNamespace(list_live=lambda: [])
    monkeypatch.setattr(_state, "pool", fake_pool)
    monkeypatch.setattr(_state, "scenario_pool", fake_spool)
    monkeypatch.setattr(_meta_routes, "PROFILES_DIR", tmp_path / "profiles")
    return TestClient(_http.build_app())


def test_persona_sizes_empty_returns_empty_json(client: TestClient) -> None:
    r = client.get("/api/personas/sizes")
    assert r.status_code == 200
    assert r.json() == {}


def test_persona_detail_404_when_missing(client: TestClient) -> None:
    r = client.get("/api/personas/ghost")
    assert r.status_code == 404


def test_persona_update_rejects_bad_yaml(client: TestClient, tmp_path: Path) -> None:
    pdir = _meta_routes.PROFILES_DIR / "alice"
    pdir.mkdir(parents=True)
    (pdir / "profile.yaml").write_text("name: alice\n")
    r = client.put("/api/personas/alice", json={"yaml": "name: [broken"})
    assert r.status_code == 400
    assert "invalid YAML" in r.json()["error"]
