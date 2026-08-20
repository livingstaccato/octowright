# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import socket
import types
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from octowright.http import app as _http_app
from octowright.http import lifespan as _http_lifespan
from octowright.http import state as _http_state


def test_get_mcp_active_session_count_returns_zero_when_tracker_is_none() -> None:
    _http_app._session_tracker = None
    assert _http_app.get_mcp_active_session_count() == 0


def test_get_mcp_active_session_count_returns_tracker_count() -> None:
    from octowright.http.mcp_session_tracker import McpSessionTracker

    tracker = McpSessionTracker()
    tracker.mark_active("a")
    tracker.mark_active("b")
    _http_app._session_tracker = tracker
    try:
        assert _http_app.get_mcp_active_session_count() == 2
    finally:
        _http_app._session_tracker = None


def test_build_app_mcp_leader_mounts_mcp_and_sets_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mcp_app = Starlette(routes=[Route("/", lambda _req: JSONResponse({"ok": True}))])
    app_kwargs: dict[str, object] = {}

    def _streamable_http_app(**kwargs: object) -> Starlette:
        app_kwargs.update(kwargs)
        return fake_mcp_app

    fake_mcp_module = types.SimpleNamespace(streamable_http_app=_streamable_http_app)
    monkeypatch.setattr("octowright.server.mcp", fake_mcp_module)

    app = _http_app.build_app(mcp_leader=True)

    # MCP 2.0: the inner route path is a streamable_http_app() kwarg, not a
    # mutable settings attribute. "/" keeps the endpoint at /mcp, not /mcp/mcp.
    assert app_kwargs["streamable_http_path"] == "/"
    assert app.router.lifespan_context == fake_mcp_app.router.lifespan_context
    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)
    # Fresh tracker has no sessions yet — the count rises when middleware sees
    # an Mcp-Session-Id header on a real request.
    assert _http_app.get_mcp_active_session_count() == 0


def test_build_app_non_leader_clears_session_tracker() -> None:
    from octowright.http.mcp_session_tracker import McpSessionTracker

    _http_app._session_tracker = McpSessionTracker()
    _http_app._session_tracker.mark_active("stale")

    _http_app.build_app(mcp_leader=False)

    assert _http_app.get_mcp_active_session_count() == 0


def test_health_route_survives_unreadable_package_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness probes depend on this answering, and the RUNNING version is a
    module constant, so unreadable metadata can no longer affect it.

    This used to assert ``version == "unknown"`` -- which was the bug: the
    version came from on-disk metadata rather than from the running process,
    so the endpoint reported whatever was installed and a read failure erased
    the answer entirely.
    """
    from octowright.version import VERSION

    def _raise(_name: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("importlib.metadata.version", _raise)
    with TestClient(_http_app.build_app()) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "version": VERSION}


def test_health_route_flags_an_upgrade_waiting_on_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """A newer package on disk is the "restart to pick it up" signal -- reported
    separately, never as the running version."""
    from octowright.version import VERSION

    monkeypatch.setattr("importlib.metadata.version", lambda _name: "99.0.0")
    with TestClient(_http_app.build_app()) as client:
        body = client.get("/api/health").json()

    assert body["version"] == VERSION
    assert body["installed_version"] == "99.0.0"


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 is not available")
def test_port_is_free_supports_ipv6_loopback() -> None:
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.bind(("::1", 0))
    busy = int(s.getsockname()[1])
    try:
        assert _http_lifespan._port_is_free("::1", busy) is False
        chosen = _http_lifespan._pick_port("::1", busy, retries=20)
        assert chosen is not None
        assert chosen > busy
    finally:
        s.close()


def test_port_is_free_requires_all_resolved_addresses_to_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    addrinfos = [
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 8765, 0, 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 8765, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 8765)),
    ]
    bind_attempts: list[tuple[object, ...]] = []

    class _FakeSocket:
        def __init__(self, family: int, socktype: int, proto: int) -> None:
            self.family = family
            self.socktype = socktype
            self.proto = proto

        def setsockopt(self, *_args: object) -> None:
            pass

        def bind(self, sockaddr: tuple[object, ...]) -> None:
            bind_attempts.append(sockaddr)
            if self.family == socket.AF_INET:
                raise OSError("IPv4 localhost port is busy")

        def close(self) -> None:
            pass

    monkeypatch.setattr(_http_lifespan.socket, "getaddrinfo", lambda *_args, **_kwargs: addrinfos)
    monkeypatch.setattr(_http_lifespan.socket, "socket", _FakeSocket)

    assert _http_lifespan._port_is_free("localhost", 8765) is False
    assert bind_attempts == [
        ("::1", 8765, 0, 0),
        ("127.0.0.1", 8765),
    ]


@pytest.mark.asyncio
async def test_serve_app_sets_runtime_and_calls_on_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    bound_calls: list[tuple[str, int]] = []

    class _FakeConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _FakeServer:
        def __init__(self, config: _FakeConfig) -> None:
            self.config = config

        async def serve(self, **_kwargs: object) -> None:
            assert _http_state._RUNTIME_HOST == "127.0.0.1"
            assert _http_state._RUNTIME_PORT == 8123

    fake_uvicorn = types.SimpleNamespace(Config=_FakeConfig, Server=_FakeServer)
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(_http_lifespan, "_pick_port", lambda *_args, **_kwargs: 8123)
    monkeypatch.setattr(_http_lifespan, "_bind_server_socket", lambda *_args, **_kwargs: None)

    def _on_bound(host: str, port: int) -> None:
        bound_calls.append((host, port))

    await _http_lifespan.serve_app(host="127.0.0.1", port=8000, retries=0, on_bound=_on_bound)

    assert bound_calls == [("127.0.0.1", 8123)]
    assert _http_state._RUNTIME_HOST is None
    assert _http_state._RUNTIME_PORT is None
    assert _http_state._RUNTIME_ERROR is None


@pytest.mark.asyncio
async def test_serve_app_sets_runtime_error_when_no_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http_lifespan, "_pick_port", lambda *_args, **_kwargs: None)

    await _http_lifespan.serve_app(host="127.0.0.1", port=8900, retries=2)

    assert _http_state._RUNTIME_ERROR == "port 8900 (and 2 fallbacks) all in use; HTTP debugger disabled"


@pytest.mark.asyncio
async def test_serve_app_cleans_runtime_state_on_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class _FakeServer:
        def __init__(self, config: _FakeConfig) -> None:
            self.config = config

        async def serve(self, **_kwargs: object) -> None:
            raise RuntimeError("serve failed")

    fake_uvicorn = types.SimpleNamespace(Config=_FakeConfig, Server=_FakeServer)
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(_http_lifespan, "_pick_port", lambda *_args, **_kwargs: 9001)
    monkeypatch.setattr(_http_lifespan, "_bind_server_socket", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="serve failed"):
        await _http_lifespan.serve_app(host="127.0.0.1", port=9000, retries=0)

    assert _http_state._RUNTIME_HOST is None
    assert _http_state._RUNTIME_PORT is None
