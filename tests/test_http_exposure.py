# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from starlette.testclient import TestClient

from octowright import http as _http
from octowright.http.exposure import is_loopback_host


def test_is_loopback_host_accepts_loopback_names_and_addresses() -> None:
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("::1") is True


def test_is_loopback_host_rejects_remote_binds() -> None:
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("::") is False
    assert is_loopback_host("192.168.1.20") is False


def test_sensitive_route_denied_on_remote_bind_without_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    app = _http.build_app()
    app.state.octowright_http_host = "0.0.0.0"

    with TestClient(app) as client:
        response = client.get("/api/sessions")

    assert response.status_code == 403
    assert response.json() == {
        "error": "remote dashboard access is disabled",
        "hint": "Bind the HTTP dashboard to 127.0.0.1 or set OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1.",
    }


def test_sensitive_route_allowed_on_remote_bind_with_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", "1")
    app = _http.build_app()
    app.state.octowright_http_host = "0.0.0.0"

    with TestClient(app) as client:
        response = client.get("/api/sessions")

    assert response.status_code == 200
