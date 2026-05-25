# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for the ``OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD`` trust boundary.

Covers three layers:

* ``is_loopback_host`` — pure host classifier
* ``_leader_url_is_safe`` / ``resolve_leader_url`` — bridge follower gate
  that refuses to MCP-bridge to a non-loopback leader URL unless the flag
  is set (defends against a lockfile-redirect MITM by a malicious local
  process running as the same user)
* ``guard_sensitive_http`` — Starlette ASGI guard that mirrors the same
  policy on the HTTP layer
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from octowright import proxy_supervisor, singleton
from octowright.defaults import DASHBOARD_REMOTE_ALLOWED_ENV
from octowright.http.exposure import guard_sensitive_http, is_loopback_host
from octowright.proxy_supervisor import _leader_url_is_safe, resolve_leader_url
from octowright.singleton import LeaderInfo

REMOTE_DISABLED_BODY = {
    "error": "remote dashboard access is disabled",
    "hint": "Bind the HTTP dashboard to 127.0.0.1 or set OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1.",
}


# ---------------------------------------------------------------------------
# is_loopback_host (pure)
# ---------------------------------------------------------------------------


def test_is_loopback_host_accepts_127001_and_localhost_and_ipv6_loopback() -> None:
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("[::1]") is True
    assert is_loopback_host("::ffff:127.0.0.1") is True


def test_is_loopback_host_rejects_lan_and_public_addresses() -> None:
    assert is_loopback_host("192.168.1.1") is False
    assert is_loopback_host("10.0.0.1") is False
    assert is_loopback_host("1.2.3.4") is False
    assert is_loopback_host("evil.com") is False


def test_is_loopback_host_rejects_garbage() -> None:
    assert is_loopback_host(None) is False
    assert is_loopback_host("") is False
    assert is_loopback_host("not-an-address") is False
    # Host strings should never carry a port; the classifier must reject
    # the combined value rather than silently strip it.
    assert is_loopback_host("127.0.0.1:8080") is False


# ---------------------------------------------------------------------------
# _leader_url_is_safe (new bridge gate)
# ---------------------------------------------------------------------------


def test_leader_url_safe_for_loopback_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    assert _leader_url_is_safe("http://127.0.0.1:8765/mcp/") is True
    assert _leader_url_is_safe("http://localhost:8765/mcp/") is True
    assert _leader_url_is_safe("http://[::1]:8765/mcp/") is True


def test_leader_url_unsafe_for_non_loopback_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    assert _leader_url_is_safe("http://evil.com:8765/mcp/") is False
    assert _leader_url_is_safe("http://10.0.0.1:8765/mcp/") is False


def test_leader_url_safe_for_non_loopback_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DASHBOARD_REMOTE_ALLOWED_ENV, "1")
    assert _leader_url_is_safe("http://evil.com:8765/mcp/") is True
    assert _leader_url_is_safe("http://10.0.0.1:8765/mcp/") is True


def test_leader_url_unsafe_for_userinfo_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Critical: urlparse extracts ``evil.com`` as hostname from
    ``http://127.0.0.1@evil.com/mcp/``. The userinfo segment must not let
    a lockfile-redirect attacker bypass the loopback check."""
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    assert _leader_url_is_safe("http://127.0.0.1@evil.com/mcp/") is False


def test_leader_url_unsafe_for_malformed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    assert _leader_url_is_safe("not-a-url") is False
    assert _leader_url_is_safe("mcp//foo") is False
    assert _leader_url_is_safe("") is False


# ---------------------------------------------------------------------------
# resolve_leader_url (integration glue: lockfile + safety + fallback)
# ---------------------------------------------------------------------------


class _LogCapture:
    """Minimal stand-in for caplog that captures structured kwargs.

    Mirrors the pattern in ``tests/test_pool_disconnect.py`` — patching the
    module-level ``log`` is robust across platforms where pytest's caplog
    misses provide.telemetry-routed records.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def debug(self, event: str, **kw: Any) -> None:
        self.events.append(("debug", event, kw))

    def info(self, event: str, **kw: Any) -> None:
        self.events.append(("info", event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append(("warning", event, kw))


def _fake_leader(mcp_url: str) -> LeaderInfo:
    return LeaderInfo(
        pid=12345,
        http_host="127.0.0.1",
        http_port=8765,
        mcp_url=mcp_url,
        started_at=0.0,
    )


def test_resolve_leader_url_returns_lockfile_url_when_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    info = _fake_leader("http://127.0.0.1:8765/mcp/")
    monkeypatch.setattr(singleton, "read_lock", lambda: info)
    monkeypatch.setattr(singleton, "is_stale", lambda _info: False)

    assert resolve_leader_url("http://fallback:1/mcp/") == "http://127.0.0.1:8765/mcp/"


def test_resolve_leader_url_falls_back_when_lockfile_url_is_non_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    info = _fake_leader("http://evil.example:8765/mcp/")
    monkeypatch.setattr(singleton, "read_lock", lambda: info)
    monkeypatch.setattr(singleton, "is_stale", lambda _info: False)
    cap = _LogCapture()
    monkeypatch.setattr(proxy_supervisor, "log", cap)

    fallback = "http://127.0.0.1:9999/mcp/"
    assert resolve_leader_url(fallback) == fallback
    rejected = [evt for evt in cap.events if evt[1] == "octowright.bridge.leader_url_rejected"]
    assert rejected, f"expected leader_url_rejected warning; got {cap.events}"
    level, _name, kwargs = rejected[0]
    assert level == "warning"
    assert kwargs.get("mcp_url") == "http://evil.example:8765/mcp/"


def test_resolve_leader_url_uses_remote_url_when_flag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DASHBOARD_REMOTE_ALLOWED_ENV, "1")
    info = _fake_leader("http://evil.example:8765/mcp/")
    monkeypatch.setattr(singleton, "read_lock", lambda: info)
    monkeypatch.setattr(singleton, "is_stale", lambda _info: False)

    assert resolve_leader_url("http://fallback:1/mcp/") == "http://evil.example:8765/mcp/"


def test_resolve_leader_url_falls_back_when_lockfile_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale lockfile short-circuits before the safety check fires: the
    fallback wins regardless of whether the recorded URL would have been
    safe."""
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    info = _fake_leader("http://127.0.0.1:8765/mcp/")
    monkeypatch.setattr(singleton, "read_lock", lambda: info)
    monkeypatch.setattr(singleton, "is_stale", lambda _info: True)

    fallback = "http://127.0.0.1:9999/mcp/"
    assert resolve_leader_url(fallback) == fallback


# ---------------------------------------------------------------------------
# guard_sensitive_http (HTTP-layer mirror of the policy)
# ---------------------------------------------------------------------------


def _build_guarded_app(host: str) -> Starlette:
    """Tiny Starlette app with one guarded no-op route. Mirrors the
    minimum surface ``guard_sensitive_http`` needs: ``app.state``-carried
    ``octowright_http_host`` is the only signal the guard reads."""

    async def _handler(_request: Request) -> Response:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/probe", guard_sensitive_http(_handler))])
    app.state.octowright_http_host = host
    return app


def test_guard_sensitive_http_rejects_when_app_bound_to_non_loopback_and_flag_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    with TestClient(_build_guarded_app("0.0.0.0")) as client:
        response = client.get("/probe")

    assert response.status_code == 403
    assert response.json() == REMOTE_DISABLED_BODY


def test_guard_sensitive_http_accepts_when_host_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DASHBOARD_REMOTE_ALLOWED_ENV, raising=False)
    with TestClient(_build_guarded_app("127.0.0.1")) as client:
        response = client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_guard_sensitive_http_accepts_when_flag_set_even_for_non_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DASHBOARD_REMOTE_ALLOWED_ENV, "1")
    with TestClient(_build_guarded_app("0.0.0.0")) as client:
        response = client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
