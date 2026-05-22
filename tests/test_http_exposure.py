# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from octowright import http as _http
from octowright.http import state as _http_state
from octowright.http.exposure import is_loopback_host

SENSITIVE_HTTP_ROUTES = [
    ("GET", "/api/sessions"),
    ("POST", "/api/sessions"),
    ("GET", "/api/sessions/s1"),
    ("DELETE", "/api/sessions/s1"),
    ("POST", "/api/sessions/s1/navigate"),
    ("DELETE", "/api/sessions/s1/recording"),
    ("POST", "/api/sessions/s1/relaunch"),
    ("GET", "/api/sessions/s1/events"),
    ("GET", "/api/dashboard/events"),
    ("GET", "/api/sessions/s1/console"),
    ("GET", "/api/sessions/s1/downloads"),
    ("GET", "/api/sessions/s1/frame"),
    ("GET", "/api/sessions/s1/video"),
    ("GET", "/api/sessions/s1/trace"),
    ("GET", "/api/sessions/s1/markdown"),
    ("POST", "/api/sessions/s1/trace/open"),
    ("GET", "/api/sessions/s1/screenshot/now"),
    ("GET", "/api/sessions/s1/screenshots"),
    ("GET", "/api/sessions/s1/screenshots/shot.png"),
    ("GET", "/api/scenarios"),
    ("POST", "/api/scenarios/demo/start"),
    ("DELETE", "/api/scenarios/sc1"),
    ("POST", "/api/scenarios/sc1/run_macro"),
    ("GET", "/api/personas"),
    ("GET", "/api/personas/sizes"),
    ("GET", "/api/personas/cosmo"),
    ("PUT", "/api/personas/cosmo"),
    ("GET", "/api/macros"),
    ("GET", "/api/macros/login"),
    ("PUT", "/api/macros/login"),
    ("POST", "/api/macros/login/validate"),
    ("GET", "/api/macros/login/repair_preview"),
    ("GET", "/api/macros/login%2Ftest/repair_preview"),
    ("GET", "/api/macros/login%2Ftest"),
    ("PUT", "/api/macros/login%2Ftest"),
    ("POST", "/api/macros/login%2Ftest/validate"),
    ("POST", "/api/sessions/s1/selector/validate"),
]

SENSITIVE_ROUTE_PATTERNS = {
    ("GET", "/api/sessions"),
    ("POST", "/api/sessions"),
    ("GET", "/api/sessions/{id}"),
    ("DELETE", "/api/sessions/{id}"),
    ("POST", "/api/sessions/{id}/navigate"),
    ("DELETE", "/api/sessions/{id}/recording"),
    ("POST", "/api/sessions/{id}/relaunch"),
    ("GET", "/api/sessions/{id}/events"),
    ("GET", "/api/dashboard/events"),
    ("GET", "/api/sessions/{id}/console"),
    ("GET", "/api/sessions/{id}/downloads"),
    ("GET", "/api/sessions/{id}/frame"),
    ("GET", "/api/sessions/{id}/video"),
    ("GET", "/api/sessions/{id}/trace"),
    ("GET", "/api/sessions/{id}/markdown"),
    ("POST", "/api/sessions/{id}/trace/open"),
    ("GET", "/api/sessions/{id}/screenshot/now"),
    ("GET", "/api/sessions/{id}/screenshots"),
    ("GET", "/api/sessions/{id}/screenshots/{filename}"),
    ("GET", "/api/scenarios"),
    ("POST", "/api/scenarios/{name}/start"),
    ("DELETE", "/api/scenarios/{id}"),
    ("POST", "/api/scenarios/{id}/run_macro"),
    ("GET", "/api/personas"),
    ("GET", "/api/personas/sizes"),
    ("GET", "/api/personas/{name}"),
    ("PUT", "/api/personas/{name}"),
    ("GET", "/api/macros"),
    ("GET", "/api/macros/{name:path}"),
    ("PUT", "/api/macros/{name:path}"),
    ("POST", "/api/macros/{name:path}/validate"),
    ("GET", "/api/macros/{name:path}/repair_preview"),
    ("POST", "/api/sessions/{id}/selector/validate"),
}

SENSITIVE_WEBSOCKET_ROUTE_PATTERNS = {
    ("WEBSOCKET", "/api/sessions/{id}/tail"),
}

PUBLIC_API_ROUTE_PATTERNS = {
    ("GET", "/api/health"),
    ("GET", "/api/metrics"),
}

REMOTE_DISABLED_BODY = {
    "error": "remote dashboard access is disabled",
    "hint": "Bind the HTTP dashboard to 127.0.0.1 or set OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1.",
}


def _remote_app() -> Any:
    app = _http.build_app()
    app.state.octowright_http_host = "0.0.0.0"
    return app


def _install_fake_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeMcpSettings:
        streamable_http_path = "unchanged"

    fake_mcp_app = Starlette(routes=[Route("/", lambda _req: JSONResponse({"mcp": True}))])
    monkeypatch.setattr(
        "octowright.server.mcp",
        types.SimpleNamespace(
            settings=_FakeMcpSettings(),
            streamable_http_app=lambda: fake_mcp_app,
        ),
    )


def test_is_loopback_host_accepts_loopback_names_and_addresses() -> None:
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("::1") is True


def test_is_loopback_host_rejects_remote_binds() -> None:
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("::") is False
    assert is_loopback_host("192.168.1.20") is False


def test_api_routes_are_explicitly_guarded_or_public(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults as _defaults

    monkeypatch.setattr(_defaults, "HTTP_METRICS_ENABLED", True)
    app = _http.build_app()
    api_routes: set[tuple[str, str]] = set()
    guarded: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, Route | WebSocketRoute):
            continue
        if not route.path.startswith("/api/"):
            continue
        if isinstance(route, WebSocketRoute):
            api_routes.add(("WEBSOCKET", route.path))
            continue
        endpoint = route.endpoint
        for method in route.methods or ():
            if method != "HEAD":
                route_key = (method, route.path)
                api_routes.add(route_key)
                if hasattr(endpoint, "__wrapped__"):
                    guarded.add(route_key)

    assert api_routes == SENSITIVE_ROUTE_PATTERNS | SENSITIVE_WEBSOCKET_ROUTE_PATTERNS | PUBLIC_API_ROUTE_PATTERNS
    assert guarded == SENSITIVE_ROUTE_PATTERNS


def test_frontend_ci_runs_biome_lint() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "npm run lint" in workflow


@pytest.mark.parametrize(("method", "path"), SENSITIVE_HTTP_ROUTES)
def test_sensitive_http_routes_denied_on_remote_bind_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)

    with TestClient(_remote_app()) as client:
        response = client.request(method, path, json={})

    assert response.status_code == 403
    assert response.json() == REMOTE_DISABLED_BODY


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/sessions"),
        ("POST", "/api/sessions/s1/navigate"),
        ("POST", "/api/scenarios/sc1/run_macro"),
        ("PUT", "/api/personas/cosmo"),
        ("GET", "/api/sessions/s1/video"),
        ("GET", "/api/sessions/s1/events"),
    ],
)
def test_representative_sensitive_routes_allowed_on_remote_bind_with_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", "1")

    with TestClient(_remote_app()) as client:
        response = client.request(method, path, json={})

    assert response.status_code != 403
    if response.headers.get("content-type", "").startswith("application/json"):
        assert response.json() != REMOTE_DISABLED_BODY


def test_tail_websocket_denied_on_remote_bind_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)

    with (
        TestClient(_remote_app()) as client,
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/sessions/s1/tail"),
    ):
        pass

    assert exc.value.code == 1008
    assert exc.value.reason == "remote dashboard access is disabled"


def test_health_route_is_unguarded_on_remote_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)

    with TestClient(_remote_app()) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_cross_origin_unsafe_api_request_is_rejected() -> None:
    with TestClient(_http.build_app()) as client:
        response = client.post(
            "/api/sessions",
            json={"kind": "chromium"},
            headers={"origin": "https://evil.example"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "cross-origin dashboard request is blocked"


def test_same_origin_unsafe_api_request_is_allowed() -> None:
    with TestClient(_http.build_app()) as client:
        response = client.post(
            "/api/sessions/s1/navigate",
            json={"url": "https://example.com"},
            headers={"origin": "http://testserver"},
        )

    assert response.status_code != 403


@pytest.mark.parametrize(
    ("host", "origin", "expected_blocked"),
    [
        ("localhost:8765", "http://localhost:8765", False),
        ("127.0.0.1:8765", "http://127.0.0.1:8765", False),
        ("localhost:8765", "http://localhost:9999", True),
        ("127.0.0.1:8765", "http://localhost:8765", True),
    ],
)
def test_origin_host_and_port_matching_for_unsafe_requests(
    host: str,
    origin: str,
    expected_blocked: bool,
) -> None:
    with TestClient(_http.build_app()) as client:
        response = client.post(
            "/api/sessions/s1/navigate",
            json={"url": "https://example.com"},
            headers={"origin": origin, "host": host},
        )
    if expected_blocked:
        assert response.status_code == 403
        assert response.json()["error"] == "cross-origin dashboard request is blocked"
    else:
        assert response.status_code != 403


def test_fetch_metadata_cross_site_unsafe_api_request_is_rejected() -> None:
    with TestClient(_http.build_app()) as client:
        response = client.post(
            "/api/sessions",
            json={"kind": "chromium"},
            headers={"sec-fetch-site": "cross-site"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "cross-origin dashboard request is blocked"


def test_public_static_assets_are_unguarded_on_remote_bind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    bundle = tmp_path / "frontend"
    bundle.mkdir()
    (bundle / "index.html").write_text("<!doctype html><title>dashboard</title>", encoding="utf-8")
    (bundle / "app.js").write_text("console.log('public asset')", encoding="utf-8")
    monkeypatch.setattr(_http_state, "FRONTEND_DIR", bundle)

    with TestClient(_remote_app()) as client:
        index_response = client.get("/")
        asset_response = client.get("/app.js")

    assert index_response.status_code == 200
    assert "dashboard" in index_response.text
    assert asset_response.status_code == 200
    assert "public asset" in asset_response.text


def test_mcp_mount_denied_on_remote_bind_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True)
    app.state.octowright_http_host = "0.0.0.0"

    with TestClient(app) as client:
        response = client.get("/mcp/")

    assert response.status_code == 403
    assert response.json() == REMOTE_DISABLED_BODY


def test_mcp_mount_allowed_on_remote_bind_with_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", "1")
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True)
    app.state.octowright_http_host = "0.0.0.0"

    with TestClient(app) as client:
        response = client.get("/mcp/")

    assert response.status_code == 200
    assert response.json() == {"mcp": True}


def test_mcp_mount_allowed_on_loopback_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True)

    with TestClient(app) as client:
        response = client.get("/mcp/")

    assert response.status_code == 200
    assert response.json() == {"mcp": True}
