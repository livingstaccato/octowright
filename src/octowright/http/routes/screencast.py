# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live screencast WebSocket endpoint."""

from __future__ import annotations

import asyncio
import contextlib

from provide.telemetry import get_logger
from starlette.endpoints import WebSocketEndpoint
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from octowright.http.discovery import _live_session_or_none
from octowright.http.exposure import sensitive_allowed_for_connection, websocket_origin_allowed
from octowright.http.pairing import dashboard_websocket_auth
from octowright.session.screencast import (
    ScreencastEnded,
    ScreencastManager,
    ScreencastViewer,
    acquire_viewer,
    release_viewer,
)
from octowright.session.screencast_config import screencast_fps, screencast_quality

log = get_logger(__name__)


def _parse_requested_fps(raw_fps: str | None, cap: int) -> int:
    if raw_fps is None:
        return cap
    try:
        requested = int(raw_fps)
    except ValueError:
        return cap
    return min(cap, max(1, requested))


async def _next_frame_or_disconnect(websocket: WebSocket, viewer: ScreencastViewer) -> bytes | None:
    frame_task = asyncio.create_task(viewer.get())
    disconnect_task = asyncio.create_task(websocket.receive())
    try:
        done, _pending = await asyncio.wait(
            {frame_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnect_task in done:
            with contextlib.suppress(Exception):
                disconnect_task.result()
            return None
        return frame_task.result()
    finally:
        for task in (frame_task, disconnect_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


async def _stream_screencast(websocket: WebSocket, viewer: ScreencastViewer) -> None:
    while True:
        frame = await _next_frame_or_disconnect(websocket, viewer)
        if frame is None:
            return
        await websocket.send_bytes(frame)


class ScreencastEndpoint(WebSocketEndpoint):
    """Stream live JPEG screencast frames for one browser session."""

    encoding = None

    async def dispatch(self) -> None:
        websocket = WebSocket(self.scope, receive=self.receive, send=self.send)
        await self.on_connect(websocket)

    async def on_connect(self, websocket: WebSocket) -> None:
        if not sensitive_allowed_for_connection(websocket):
            await websocket.close(code=1008, reason="remote dashboard access is disabled")
            return
        if not websocket_origin_allowed(websocket):
            await websocket.close(code=1008, reason="cross-origin websocket handshake is blocked")
            return
        pairing_ok, selected_protocol = dashboard_websocket_auth(websocket)
        if not pairing_ok:
            await websocket.close(code=1008, reason="dashboard pairing required")
            return

        await websocket.accept(subprotocol=selected_protocol)
        sid = websocket.path_params["id"]
        live_session = _live_session_or_none(sid)
        if live_session is None:
            await websocket.close(code=1008, reason=f"no live session with id {sid}")
            return

        manager: ScreencastManager | None = None
        viewer: ScreencastViewer | None = None
        try:
            fps = _parse_requested_fps(websocket.query_params.get("fps"), screencast_fps())
            manager, viewer = await acquire_viewer(
                live_session,
                fps=fps,
                quality=screencast_quality(),
            )
        except Exception as exc:
            log.warning(
                "octowright.screencast.acquire_failed",
                session_id=sid,
                error=repr(exc),
            )
            await websocket.close(code=1011, reason="screencast unavailable; use fallback")
            return

        try:
            assert manager is not None  # nosec B101  # narrowed after successful acquire
            assert viewer is not None  # nosec B101  # narrowed after successful acquire
            await _stream_screencast(websocket, viewer)
        except ScreencastEnded:
            # The producer is gone (session closed, or a rebind could not
            # reattach). Close so the dashboard drops to screenshot polling
            # instead of holding a socket that will never carry another frame.
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="screencast ended; use fallback")
            return
        except WebSocketDisconnect:
            return
        finally:
            await release_viewer(manager, viewer)


def routes() -> list[WebSocketRoute]:
    return [
        WebSocketRoute("/api/sessions/{id}/screencast", ScreencastEndpoint),
    ]
