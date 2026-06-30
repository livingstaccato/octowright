# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
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
    ("WEBSOCKET", "/api/sessions/{id}/screencast"),
}

PUBLIC_API_ROUTE_PATTERNS = {
    ("GET", "/api/health"),
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
    assert is_loopback_host(None) is False


@pytest.mark.parametrize(
    ("raw_host", "expected"),
    [
        ("127.0.0.1:6286", True),
        ("localhost:6286", True),
        ("[::1]:6286", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("malicious.example:6286", False),
        ("192.168.1.20:6286", False),
        ("[::1", False),  # malformed bracket (no closing ]) → treated as-is, not loopback
        ("", False),
        (None, False),
    ],
)
def test_request_host_loopback_allowed_parses_host_header(raw_host: str | None, expected: bool) -> None:
    from octowright.http.exposure import request_host_loopback_allowed

    assert request_host_loopback_allowed(raw_host) is expected


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


def test_tail_websocket_rejects_cross_origin_browser_handshake() -> None:
    """A page loaded from another origin must not be able to open /tail.

    Loopback bind allows the connection at TCP level (the kernel sees both
    peers on 127.0.0.1), so the host check passes; the Origin header is the
    only signal that the handshake came from a foreign browser context.
    Refuse with 1008 before ``websocket.accept()`` so the attacker page
    never receives any JSONL data.
    """
    with (
        TestClient(_http.build_app()) as client,
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            "/api/sessions/s1/tail",
            headers={"origin": "http://evil.example", "host": "127.0.0.1:8765"},
        ),
    ):
        pass

    assert exc.value.code == 1008
    assert exc.value.reason == "cross-origin websocket handshake is blocked"


def _drain_until_disconnect(ws: Any) -> WebSocketDisconnect:
    """Consume frames from ``ws`` until the server closes; return the close.

    The ``no session with id`` rejection path runs ``accept()`` first and
    closes inside the handler, so the disconnect surfaces on the first
    ``receive_*`` call rather than on ``__enter__``.
    """
    try:
        while True:
            ws.receive_json()
    except WebSocketDisconnect as exc:
        return exc


def test_tail_websocket_allows_same_origin_browser_handshake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The dashboard SPA opens /tail with Origin matching Host — must pass."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    # No live session and no recording → handler accepts, then closes with
    # 1008 ("no session with id"). Distinguish from the cross-origin
    # pre-accept close by inspecting the reason.
    with (
        TestClient(_http.build_app()) as client,
        client.websocket_connect(
            "/api/sessions/sameorigin1/tail",
            headers={"origin": "http://127.0.0.1:8765", "host": "127.0.0.1:8765"},
        ) as ws,
    ):
        exc = _drain_until_disconnect(ws)

    assert exc.code == 1008
    assert "cross-origin" not in exc.reason


def test_tail_websocket_allows_no_origin_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """curl / python-websockets clients send no Origin — treat as non-browser."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    with (
        TestClient(_http.build_app()) as client,
        client.websocket_connect(
            "/api/sessions/noorigin001/tail",
            headers={"host": "127.0.0.1:8765"},
        ) as ws,
    ):
        exc = _drain_until_disconnect(ws)
    assert exc.code == 1008
    assert "cross-origin" not in exc.reason


def test_tail_websocket_allows_loopback_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A loopback Origin (e.g. another local dashboard tab on a different
    port) is allowed even if it doesn't match the request Host."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    with (
        TestClient(_http.build_app()) as client,
        client.websocket_connect(
            "/api/sessions/loopback001/tail",
            headers={"origin": "http://127.0.0.1:9999", "host": "127.0.0.1:8765"},
        ) as ws,
    ):
        exc = _drain_until_disconnect(ws)
    assert exc.code == 1008
    assert "cross-origin" not in exc.reason


def test_tail_websocket_rejects_malformed_origin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An Origin value with no scheme separator is treated as suspicious."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    with (
        TestClient(_http.build_app()) as client,
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(
            "/api/sessions/malformed01/tail",
            headers={"origin": "no-scheme-here", "host": "127.0.0.1:8765"},
        ),
    ):
        pass
    assert exc.value.code == 1008
    assert exc.value.reason == "cross-origin websocket handshake is blocked"


def test_tail_websocket_allows_loopback_ipv6_origin_with_bracket_and_port(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Origin like http://[::1]:9000 must be parsed as IPv6 loopback."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    with (
        TestClient(_http.build_app()) as client,
        client.websocket_connect(
            "/api/sessions/ipv6brkt001/tail",
            headers={"origin": "http://[::1]:9000", "host": "127.0.0.1:8765"},
        ) as ws,
    ):
        exc = _drain_until_disconnect(ws)
    assert exc.code == 1008
    assert "cross-origin" not in exc.reason


def test_websocket_origin_allowed_handles_bare_ipv6_without_brackets() -> None:
    """Direct unit test for the helper: bare IPv6 (no brackets, no port) loopback path."""
    from octowright.http.exposure import websocket_origin_allowed

    fake_ws = types.SimpleNamespace(headers={"origin": "http://::1", "host": "127.0.0.1:8765"})
    # Bare "::1" with three colons doesn't match the port-stripping heuristic
    # so it falls through to is_loopback_host("::1") → True.
    assert websocket_origin_allowed(fake_ws) is True  # type: ignore[arg-type]


def test_health_route_is_unguarded_on_remote_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)

    with TestClient(_remote_app()) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_dns_rebinding_host_rejected_for_sensitive_api_route() -> None:
    with TestClient(_http.build_app(host="127.0.0.1")) as client:
        response = client.post(
            "/api/sessions/s1/navigate",
            json={"url": "https://octowright.com"},
            headers={
                "host": "malicious.example:6286",
                "origin": "http://malicious.example:6286",
            },
        )

    assert response.status_code == 403
    assert response.json() == REMOTE_DISABLED_BODY


def test_cross_origin_unsafe_api_request_is_rejected() -> None:
    with TestClient(_http.build_app()) as client:
        response = client.post(
            "/api/sessions",
            json={"kind": "chromium"},
            headers={"origin": "https://evil.example", "host": "127.0.0.1:8765"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "cross-origin dashboard request is blocked"


def test_same_origin_unsafe_api_request_is_allowed() -> None:
    # A real same-origin browser request carries a loopback Host (the dashboard
    # binds to 127.0.0.1); the Origin matches it. Both must be loopback now that
    # the DNS-rebinding guard rejects non-loopback Host headers.
    with TestClient(_http.build_app()) as client:
        response = client.post(
            "/api/sessions/s1/navigate",
            json={"url": "https://octowright.com"},
            headers={"origin": "http://127.0.0.1:8765", "host": "127.0.0.1:8765"},
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
            json={"url": "https://octowright.com"},
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
            headers={"sec-fetch-site": "cross-site", "host": "127.0.0.1:8765"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "cross-origin dashboard request is blocked"


@pytest.mark.parametrize("path", ["/api/sessions/s1/screenshot/now", "/api/sessions/s1/markdown"])
def test_cross_origin_live_capture_get_request_is_rejected(path: str) -> None:
    with TestClient(_http.build_app()) as client:
        response = client.get(path, headers={"origin": "https://evil.example", "host": "127.0.0.1:8765"})

    assert response.status_code == 403
    assert response.json()["error"] == "cross-origin dashboard request is blocked"


def test_cross_origin_regular_read_get_request_is_allowed() -> None:
    with TestClient(_http.build_app()) as client:
        response = client.get("/api/sessions", headers={"origin": "https://evil.example", "host": "127.0.0.1:8765"})

    assert response.status_code != 403


def test_spa_denied_on_remote_bind_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bundled SPA is sensitive: serving it on a non-loopback bind without
    OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD confirms the daemon to anyone who can
    reach the port and exposes the dashboard surface."""
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    bundle = tmp_path / "frontend"
    bundle.mkdir()
    (bundle / "index.html").write_text("<!doctype html><title>dashboard</title>", encoding="utf-8")
    (bundle / "app.js").write_text("console.log('public asset')", encoding="utf-8")
    monkeypatch.setattr(_http_state, "FRONTEND_DIR", bundle)

    # SPA guard reads the bind host from a wrap-time closure (StaticFiles
    # is an ASGI app, not a Request handler), so we must build the app at
    # the remote host directly rather than post-build override app.state.
    app = _http.build_app(host="0.0.0.0")
    with TestClient(app) as client:
        index_response = client.get("/")
        asset_response = client.get("/app.js")
        deep_link_response = client.get("/sessions/abc123")

    assert index_response.status_code == 403
    assert index_response.json() == REMOTE_DISABLED_BODY
    assert asset_response.status_code == 403
    assert asset_response.json() == REMOTE_DISABLED_BODY
    assert deep_link_response.status_code == 403
    assert deep_link_response.json() == REMOTE_DISABLED_BODY


def test_dns_rebinding_host_rejected_for_spa_static_mount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    bundle = tmp_path / "frontend"
    bundle.mkdir()
    (bundle / "index.html").write_text("<!doctype html><title>dashboard</title>", encoding="utf-8")
    monkeypatch.setattr(_http_state, "FRONTEND_DIR", bundle)

    app = _http.build_app(host="127.0.0.1")
    with TestClient(app) as client:
        response = client.get(
            "/",
            headers={
                "host": "malicious.example:6286",
                "origin": "http://malicious.example:6286",
            },
        )

    assert response.status_code == 403
    assert response.json() == REMOTE_DISABLED_BODY


def test_spa_allowed_on_loopback_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Loopback bind keeps the SPA serving — the only-localhost-can-reach-it
    threat model is unchanged."""
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    bundle = tmp_path / "frontend"
    bundle.mkdir()
    (bundle / "index.html").write_text("<!doctype html><title>dashboard</title>", encoding="utf-8")
    (bundle / "app.js").write_text("console.log('public asset')", encoding="utf-8")
    monkeypatch.setattr(_http_state, "FRONTEND_DIR", bundle)

    app = _http.build_app(host="127.0.0.1")
    with TestClient(app) as client:
        index_response = client.get("/", headers={"host": "127.0.0.1:8765"})
        asset_response = client.get("/app.js", headers={"host": "127.0.0.1:8765"})

    assert index_response.status_code == 200
    assert "dashboard" in index_response.text
    assert asset_response.status_code == 200
    assert "public asset" in asset_response.text


def test_spa_allowed_on_remote_bind_with_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Remote bind plus OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1 lets the SPA
    through, matching the documented opt-in behaviour for the API surface."""
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", "1")
    bundle = tmp_path / "frontend"
    bundle.mkdir()
    (bundle / "index.html").write_text("<!doctype html><title>dashboard</title>", encoding="utf-8")
    monkeypatch.setattr(_http_state, "FRONTEND_DIR", bundle)

    app = _http.build_app(host="0.0.0.0")
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "dashboard" in response.text


def test_mcp_mount_denied_on_remote_bind_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True, host="0.0.0.0")

    with TestClient(app) as client:
        response = client.get("/mcp/")

    assert response.status_code == 403
    assert response.json() == REMOTE_DISABLED_BODY


def test_mcp_mount_allowed_on_remote_bind_with_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", "1")
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True, host="0.0.0.0")

    with TestClient(app) as client:
        response = client.get("/mcp/")

    assert response.status_code == 200
    assert response.json() == {"mcp": True}


def test_mcp_mount_allowed_on_loopback_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True)

    with TestClient(app) as client:
        response = client.get("/mcp/", headers={"host": "127.0.0.1:8765"})

    assert response.status_code == 200
    assert response.json() == {"mcp": True}


def test_dns_rebinding_host_rejected_for_mcp_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True, host="127.0.0.1")

    with TestClient(app) as client:
        response = client.get(
            "/mcp/",
            headers={
                "host": "malicious.example:6286",
                "origin": "http://malicious.example:6286",
            },
        )

    assert response.status_code == 403
    assert response.json() == REMOTE_DISABLED_BODY


def test_dns_rebinding_host_allowed_for_mcp_mount_with_remote_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", "1")
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True, host="127.0.0.1")

    with TestClient(app) as client:
        response = client.get(
            "/mcp/",
            headers={
                "host": "malicious.example:6286",
                "origin": "http://malicious.example:6286",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"mcp": True}


def test_cross_origin_unsafe_request_blocked_for_mcp_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    """A loopback Host passes the rebinding guard, but a foreign Origin on an
    unsafe (POST) request to the mounted ASGI app is still cross-origin blocked."""
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    _install_fake_mcp(monkeypatch)
    app = _http.build_app(mcp_leader=True, host="127.0.0.1")

    with TestClient(app) as client:
        response = client.post(
            "/mcp/",
            headers={"host": "127.0.0.1:8765", "origin": "http://evil.example"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "cross-origin dashboard request is blocked"


# --- Direct unit coverage for the exposure helpers and ASGI guard branches ---


def _collecting_send() -> tuple[list[dict[str, Any]], Any]:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    return messages, send


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.request"}


def test_cross_origin_blocked_from_parts_allows_cors_preflight() -> None:
    from octowright.http.exposure import _cross_origin_blocked_from_parts

    assert (
        _cross_origin_blocked_from_parts(
            method="OPTIONS",
            origin="http://evil.example",
            sec_fetch_site="cross-site",
            scheme="http",
            host="127.0.0.1:8765",
            side_effect_get=True,
        )
        is False
    )


def test_http_scheme_for_scope_maps_schemes() -> None:
    from octowright.http.exposure import _http_scheme_for_scope

    assert _http_scheme_for_scope({"type": "websocket", "scheme": "wss"}) == "https"
    assert _http_scheme_for_scope({"type": "websocket", "scheme": "ws"}) == "http"
    assert _http_scheme_for_scope({"type": "http", "scheme": "https"}) == "https"


def test_asgi_cross_origin_blocked_parses_headers_when_not_supplied() -> None:
    """The back-compat path: a direct caller omits the parsed ``headers`` dict,
    so the function re-derives it from the scope."""
    from octowright.http.exposure import _asgi_cross_origin_blocked

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "headers": [(b"host", b"127.0.0.1:8765"), (b"origin", b"http://evil.example")],
    }
    assert _asgi_cross_origin_blocked(scope, side_effect_get=False) is True


def test_sensitive_asgi_guard_passes_through_non_http_scope() -> None:
    """Lifespan (and any non-http/websocket) scopes bypass the guard entirely."""
    from octowright.http.exposure import SensitiveASGIGuard

    seen: list[str] = []

    async def inner_app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    guard = SensitiveASGIGuard(inner_app, host="127.0.0.1")
    messages, send = _collecting_send()
    asyncio.run(guard({"type": "lifespan"}, _noop_receive, send))

    assert seen == ["lifespan"]
    assert messages == []


def test_sensitive_asgi_guard_closes_websocket_on_dns_rebinding_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-loopback Host on a websocket scope is closed with 1008 before the
    inner app runs."""
    from octowright.http.exposure import SensitiveASGIGuard

    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)

    async def inner_app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("inner app must not run for a blocked websocket")

    guard = SensitiveASGIGuard(inner_app, host="127.0.0.1")
    scope = {"type": "websocket", "headers": [(b"host", b"malicious.example:6286")]}
    messages, send = _collecting_send()
    asyncio.run(guard(scope, _noop_receive, send))

    assert messages == [{"type": "websocket.close", "code": 1008, "reason": "remote dashboard access is disabled"}]


def test_sensitive_asgi_guard_closes_cross_origin_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loopback Host passes the rebinding guard, but a foreign Origin on a
    websocket scope is closed with 1008 as cross-origin."""
    from octowright.http.exposure import SensitiveASGIGuard

    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)

    async def inner_app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("inner app must not run for a blocked websocket")

    guard = SensitiveASGIGuard(inner_app, host="127.0.0.1")
    scope = {
        "type": "websocket",
        "scheme": "ws",
        "headers": [(b"host", b"127.0.0.1:8765"), (b"origin", b"http://evil.example")],
    }
    messages, send = _collecting_send()
    asyncio.run(guard(scope, _noop_receive, send))

    assert messages == [
        {"type": "websocket.close", "code": 1008, "reason": "cross-origin dashboard request is blocked"}
    ]
