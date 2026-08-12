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
from octowright.http.pairing import (
    DASHBOARD_AUTH_EXPIRED_REASON,
    DASHBOARD_STREAM_AUTH_CHECK_SECONDS,
    DashboardStreamLease,
    dashboard_stream_lease,
    dashboard_stream_lease_valid,
    dashboard_websocket_auth,
)
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


async def _stream_screencast(
    websocket: WebSocket,
    viewer: ScreencastViewer,
    *,
    lease: DashboardStreamLease | None = None,
) -> None:
    while True:
        if not dashboard_stream_lease_valid(lease):
            await websocket.close(code=1008, reason=DASHBOARD_AUTH_EXPIRED_REASON)
            return
        try:
            if lease is not None and lease.revalidatable:
                frame = await asyncio.wait_for(
                    _next_frame_or_disconnect(websocket, viewer),
                    timeout=DASHBOARD_STREAM_AUTH_CHECK_SECONDS,
                )
            else:
                frame = await _next_frame_or_disconnect(websocket, viewer)
        except TimeoutError:
            continue
        if frame is None:
            return
        if not dashboard_stream_lease_valid(lease):
            await websocket.close(code=1008, reason=DASHBOARD_AUTH_EXPIRED_REASON)
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
            # A pre-accept rejection is exposed by Chromium as 1006 with an
            # empty reason. Accept without selecting the private credential
            # protocol. Select only the stable public protocol, then close
            # before any frame/session lookup so the
            # client receives the actionable pairing close reason.
            await websocket.accept(subprotocol=selected_protocol)
            await websocket.close(code=1008, reason="dashboard pairing required")
            return
        lease = dashboard_stream_lease(websocket)

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
            await _stream_screencast(websocket, viewer, lease=lease)
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
