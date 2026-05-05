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


def test_macro_repair_preview_endpoint_returns_preview(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _meta_routes.state._macros,
        "repair_preview",
        lambda name: {"macro": name, "suggestions": [{"action_index": 0}]},
    )

    r = client.get("/api/macros/login/repair_preview")

    assert r.status_code == 200
    assert r.json() == {"macro": "login", "suggestions": [{"action_index": 0}]}


def test_macro_repair_preview_endpoint_accepts_slash_names(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _meta_routes.state._macros,
        "repair_preview",
        lambda name: {"macro": name, "suggestions": []},
    )

    r = client.get("/api/macros/login%2Ftest/repair_preview")

    assert r.status_code == 200
    assert r.json() == {"macro": "login/test", "suggestions": []}


def test_macro_repair_preview_endpoint_404_for_unknown_macro(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> dict:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(_meta_routes.state._macros, "repair_preview", missing)

    r = client.get("/api/macros/missing/repair_preview")

    assert r.status_code == 404


def test_macro_detail_endpoint_returns_full_macro(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _meta_routes.state._macros,
        "load_macro",
        lambda name: {"name": name, "actions": [{"action": "click", "selector": "#ok"}]},
    )

    r = client.get("/api/macros/login%2Fsmoke")

    assert r.status_code == 200
    assert r.json() == {"name": "login/smoke", "actions": [{"action": "click", "selector": "#ok"}]}


def test_macro_validate_endpoint_reports_lint_issues(client: TestClient) -> None:
    r = client.post("/api/macros/login/validate", json={"macro": {"name": "login", "actions": [{"action": "click"}]}})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["issue_count"] == 1
    assert body["issues"][0]["code"] == "missing_required_field"


def test_macro_update_endpoint_rejects_invalid_macro(client: TestClient) -> None:
    r = client.put("/api/macros/login", json={"macro": {"name": "login", "actions": [{"action": "click"}]}})

    assert r.status_code == 400
    assert r.json()["issues"][0]["code"] == "missing_required_field"


def test_macro_update_endpoint_writes_macro_and_invalidates(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    macro_dir = tmp_path / "macros"
    monkeypatch.setattr(_meta_routes.state._macros, "MACROS_DIR", macro_dir)

    published: list[str] = []

    async def fake_publish(scope: str) -> None:
        published.append(scope)

    monkeypatch.setattr(_meta_routes, "publish_dashboard_invalidation", fake_publish)

    macro = {
        "name": "login",
        "description": "demo",
        "parameters": [],
        "actions": [{"action": "press_key", "key": "Escape"}],
    }
    r = client.put("/api/macros/login", json={"macro": macro})

    assert r.status_code == 200
    assert r.json()["ok"] is True
    saved = macro_dir / "login.json"
    assert saved.exists()
    assert '"press_key"' in saved.read_text(encoding="utf-8")
    assert published == ["macros"]
