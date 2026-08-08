# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from octowright.http.routes import screencast as scr


class FakeScreencast:
    def __init__(self) -> None:
        self._on_frame = None

    async def start(self, *, on_frame=None, quality=None) -> None:
        self._on_frame = on_frame
        assert quality == 70
        on_frame({"data": b"jpeg-bytes", "viewportWidth": 1, "viewportHeight": 1})

    async def stop(self) -> None:
        self._on_frame = None


class FakePage:
    def __init__(self) -> None:
        self.screencast = FakeScreencast()


class FakeSession:
    instance_id = "ws1"
    kind = "chromium"

    def __init__(self) -> None:
        self.page = FakePage()


class OneFrameViewer:
    def __init__(self, frame: bytes = b"jpeg-bytes") -> None:
        self._frame = frame
        self._sent = False
        self._blocked = asyncio.Event()

    async def get(self) -> bytes:
        if not self._sent:
            self._sent = True
            return self._frame
        await self._blocked.wait()
        raise AssertionError("unreachable")


class LogCapture:
    def __init__(self) -> None:
        self.warning_calls: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warning_calls.append((event, kwargs))


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Starlette:
    sess = FakeSession()
    monkeypatch.setattr(scr, "_live_session_or_none", lambda sid: sess if sid == "ws1" else None)
    return Starlette(routes=scr.routes())


def test_screencast_websocket_streams_binary_frame(app: Starlette) -> None:
    with TestClient(app) as client, client.websocket_connect("/api/sessions/ws1/screencast") as ws:
        assert ws.receive_bytes() == b"jpeg-bytes"


def test_screencast_websocket_unknown_session_closes_with_1008(app: Starlette) -> None:
    with TestClient(app) as client, client.websocket_connect("/api/sessions/nope/screencast") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_bytes()
        assert excinfo.value.code == 1008


def test_screencast_websocket_rejects_non_loopback_host(app: Starlette) -> None:
    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            client.websocket_connect(
                "/api/sessions/ws1/screencast",
                headers={"host": "attacker.example"},
            ),
        ):
            pass
        assert excinfo.value.code == 1008


def test_screencast_websocket_rejects_cross_origin_handshake(app: Starlette) -> None:
    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            client.websocket_connect(
                "/api/sessions/ws1/screencast",
                headers={"origin": "http://evil.example", "host": "127.0.0.1:8765"},
            ),
        ):
            pass
        assert excinfo.value.code == 1008


@pytest.mark.parametrize(
    ("query", "expected_fps"),
    [
        ("?fps=99", 5),
        ("?fps=0", 1),
        ("?fps=not-an-int", 5),
        ("", 5),
    ],
)
def test_screencast_websocket_clamps_requested_fps(
    app: Starlette,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_fps: int,
) -> None:
    calls: list[tuple[int, int]] = []
    manager = object()
    viewer = OneFrameViewer()

    async def fake_acquire_viewer(_session: object, *, fps: int, quality: int) -> tuple[object, OneFrameViewer]:
        calls.append((fps, quality))
        return manager, viewer

    async def fake_release_viewer(_manager: object, _viewer: OneFrameViewer) -> None:
        return None

    monkeypatch.setattr(scr, "screencast_fps", lambda: 5)
    monkeypatch.setattr(scr, "screencast_quality", lambda: 44)
    monkeypatch.setattr(scr, "acquire_viewer", fake_acquire_viewer)
    monkeypatch.setattr(scr, "release_viewer", fake_release_viewer)

    with TestClient(app) as client, client.websocket_connect(f"/api/sessions/ws1/screencast{query}") as ws:
        assert ws.receive_bytes() == b"jpeg-bytes"

    assert calls == [(expected_fps, 44)]


def test_screencast_websocket_acquire_failure_closes_1011_logs_and_does_not_release(
    app: Starlette,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_calls: list[tuple[object, object]] = []
    log = LogCapture()

    async def fake_acquire_viewer(_session: object, *, fps: int, quality: int) -> tuple[object, OneFrameViewer]:
        raise RuntimeError("start failed")

    async def fake_release_viewer(manager: object, viewer: object) -> None:
        release_calls.append((manager, viewer))

    monkeypatch.setattr(scr, "acquire_viewer", fake_acquire_viewer)
    monkeypatch.setattr(scr, "release_viewer", fake_release_viewer)
    monkeypatch.setattr(scr, "log", log)

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/sessions/ws1/screencast") as ws,
        pytest.raises(WebSocketDisconnect) as excinfo,
    ):
        ws.receive_bytes()

    assert excinfo.value.code == 1011
    assert release_calls == []
    assert log.warning_calls == [
        (
            "octowright.screencast.acquire_failed",
            {"session_id": "ws1", "error": "RuntimeError('start failed')"},
        )
    ]


def test_screencast_websocket_closes_1011_when_the_stream_ends_server_side(
    app: Starlette,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session close (or a failed rebind) ends the viewer; the endpoint must
    close the socket so the dashboard drops to screenshot polling."""
    manager = object()
    release_calls: list[tuple[object, object]] = []

    class EndedViewer:
        async def get(self) -> bytes:
            raise scr.ScreencastEnded("screencast ended")

    viewer = EndedViewer()

    async def fake_acquire_viewer(_session: object, *, fps: int, quality: int) -> tuple[object, EndedViewer]:
        return manager, viewer

    async def fake_release_viewer(released_manager: object, released_viewer: object) -> None:
        release_calls.append((released_manager, released_viewer))

    monkeypatch.setattr(scr, "acquire_viewer", fake_acquire_viewer)
    monkeypatch.setattr(scr, "release_viewer", fake_release_viewer)

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/sessions/ws1/screencast") as ws,
        pytest.raises(WebSocketDisconnect) as excinfo,
    ):
        ws.receive_bytes()

    assert excinfo.value.code == 1011
    assert release_calls == [(manager, viewer)]


def test_screencast_websocket_releases_viewer_on_client_disconnect(
    app: Starlette,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = object()
    viewer = OneFrameViewer()
    release_calls: list[tuple[object, OneFrameViewer]] = []

    async def fake_acquire_viewer(_session: object, *, fps: int, quality: int) -> tuple[object, OneFrameViewer]:
        return manager, viewer

    async def fake_release_viewer(released_manager: object, released_viewer: OneFrameViewer) -> None:
        release_calls.append((released_manager, released_viewer))

    monkeypatch.setattr(scr, "acquire_viewer", fake_acquire_viewer)
    monkeypatch.setattr(scr, "release_viewer", fake_release_viewer)

    with TestClient(app) as client, client.websocket_connect("/api/sessions/ws1/screencast") as ws:
        assert ws.receive_bytes() == b"jpeg-bytes"

    assert release_calls == [(manager, viewer)]


async def test_screencast_endpoint_releases_viewer_when_streaming_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    manager = object()
    viewer = OneFrameViewer()
    release_calls: list[tuple[object, OneFrameViewer]] = []

    class FakeWebSocket:
        path_params = {"id": "ws1"}
        query_params: dict[str, str] = {}

        def __init__(self) -> None:
            self.accepted = False
            self.closed: tuple[int, str] | None = None

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, *, code: int = 1000, reason: str | None = None) -> None:
            self.closed = (code, reason or "")

    async def fake_acquire_viewer(_session: object, *, fps: int, quality: int) -> tuple[object, OneFrameViewer]:
        return manager, viewer

    async def fake_release_viewer(released_manager: object, released_viewer: OneFrameViewer) -> None:
        release_calls.append((released_manager, released_viewer))

    async def fake_stream_screencast(_websocket: object, _viewer: OneFrameViewer) -> None:
        raise RuntimeError("send failed")

    monkeypatch.setattr(scr, "sensitive_allowed_for_connection", lambda _websocket: True)
    monkeypatch.setattr(scr, "websocket_origin_allowed", lambda _websocket: True)
    monkeypatch.setattr(scr, "_live_session_or_none", lambda sid: session if sid == "ws1" else None)
    monkeypatch.setattr(scr, "acquire_viewer", fake_acquire_viewer)
    monkeypatch.setattr(scr, "release_viewer", fake_release_viewer)
    monkeypatch.setattr(scr, "_stream_screencast", fake_stream_screencast)

    websocket = FakeWebSocket()

    with pytest.raises(RuntimeError, match="send failed"):
        await scr.ScreencastEndpoint.on_connect(SimpleNamespace(), websocket)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert websocket.closed is None
    assert release_calls == [(manager, viewer)]
