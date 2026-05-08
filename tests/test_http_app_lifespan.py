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


def test_get_mcp_active_session_count_handles_none_and_missing_attr() -> None:
    _http_app._mcp_session_manager = None
    assert _http_app.get_mcp_active_session_count() == 0

    _http_app._mcp_session_manager = object()
    assert _http_app.get_mcp_active_session_count() == 0


def test_get_mcp_active_session_count_reads_server_instances_len() -> None:
    _http_app._mcp_session_manager = types.SimpleNamespace(_server_instances={"a": object(), "b": object()})
    assert _http_app.get_mcp_active_session_count() == 2


def test_build_app_mcp_leader_mounts_mcp_and_sets_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeMcpSettings:
        streamable_http_path = "unchanged"

    fake_session_manager = types.SimpleNamespace(_server_instances={"x": object()})
    fake_transport = types.SimpleNamespace(session_manager=fake_session_manager)
    fake_mcp_app = Starlette(routes=[Route("/", lambda _req: JSONResponse({"ok": True}))])
    fake_mcp_app.routes[0].app = fake_transport  # type: ignore[attr-defined]

    fake_mcp_module = types.SimpleNamespace(
        settings=_FakeMcpSettings(),
        streamable_http_app=lambda: fake_mcp_app,
    )
    monkeypatch.setattr("octowright.server.mcp", fake_mcp_module)

    app = _http_app.build_app(mcp_leader=True)

    assert fake_mcp_module.settings.streamable_http_path == "/"
    assert app.router.lifespan_context == fake_mcp_app.router.lifespan_context
    assert any(getattr(route, "path", None) == "/mcp" for route in app.routes)
    assert _http_app.get_mcp_active_session_count() == 1


def test_build_app_mcp_leader_tolerates_missing_mcp_route_app(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeMcpSettings:
        streamable_http_path = "unchanged"

    fake_mcp_app = Starlette(routes=[])
    fake_mcp_module = types.SimpleNamespace(
        settings=_FakeMcpSettings(),
        streamable_http_app=lambda: fake_mcp_app,
    )
    monkeypatch.setattr("octowright.server.mcp", fake_mcp_module)

    app = _http_app.build_app(mcp_leader=True)

    assert app.router.lifespan_context == fake_mcp_app.router.lifespan_context


def test_build_app_non_leader_clears_stale_mcp_session_manager() -> None:
    _http_app._mcp_session_manager = types.SimpleNamespace(_server_instances={"stale": object()})

    _http_app.build_app(mcp_leader=False)

    assert _http_app.get_mcp_active_session_count() == 0


def test_health_route_returns_unknown_when_metadata_version_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_name: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr("importlib.metadata.version", _raise)
    with TestClient(_http_app.build_app()) as client:
        res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["version"] == "unknown"


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

        async def serve(self) -> None:
            assert _http_state._RUNTIME_HOST == "127.0.0.1"
            assert _http_state._RUNTIME_PORT == 8123

    fake_uvicorn = types.SimpleNamespace(Config=_FakeConfig, Server=_FakeServer)
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(_http_lifespan, "_pick_port", lambda *_args, **_kwargs: 8123)

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

        async def serve(self) -> None:
            raise RuntimeError("serve failed")

    fake_uvicorn = types.SimpleNamespace(Config=_FakeConfig, Server=_FakeServer)
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(_http_lifespan, "_pick_port", lambda *_args, **_kwargs: 9001)

    with pytest.raises(RuntimeError, match="serve failed"):
        await _http_lifespan.serve_app(host="127.0.0.1", port=9000, retries=0)

    assert _http_state._RUNTIME_HOST is None
    assert _http_state._RUNTIME_PORT is None
