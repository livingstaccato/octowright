# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tiny Starlette app that backs the demo playground.

Two responsibilities:

1. **Static serve** the playground HTML/CSS/JS so all demo bundles can target
   ``http://127.0.0.1:7900/<page>.html``.
2. **In-memory state store** with REST writes + Server-Sent-Events reads so
   multiple browser instances can see each other's actions in real time.
   This is what makes "9 browsers visibly coordinating" actually visible on
   video — without shared state, each window does its own thing and the
   parallelism doesn't read.

The server resets state on every startup so recordings are reproducible.
No persistence; no auth; loopback-only by default. If you need a different
port (collision with a local dev process), set ``OCTOWRIGHT_PLAYGROUND_PORT``.

Lifecycle (matches ``scripts/demos/with_playground.py``):
    server = PlaygroundServer()
    await server.start()
    try:
        ...record demo...
    finally:
        await server.stop()
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

DEFAULT_PORT = 7900
PLAYGROUND_PORT_ENV = "OCTOWRIGHT_PLAYGROUND_PORT"

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _resolve_port() -> int:
    raw = os.environ.get(PLAYGROUND_PORT_ENV)
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


class _State:
    """Shared playground state. Two demo workloads live here:

    * **canvas tiles** — a 10x10 grid where each cell is either ``None`` (free)
      or a colour string claimed by one of the canvas-demo participants.
    * **form steps** — a list of step events written by the form-flow page and
      streamed to the monitor dashboard.

    Every mutation pushes an event to every connected SSE subscriber. The
    payload shape is intentionally simple so the playground HTML can render it
    without a JS framework.
    """

    GRID_SIZE = 10

    def __init__(self) -> None:
        self.canvas: list[list[str | None]] = [[None] * self.GRID_SIZE for _ in range(self.GRID_SIZE)]
        self.form_steps: list[dict[str, Any]] = []
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict[str, Any]:
        return {"canvas": self.canvas, "form_steps": list(self.form_steps)}

    async def claim_tile(self, row: int, col: int, colour: str, claimed_by: str) -> dict[str, Any]:
        if not 0 <= row < self.GRID_SIZE or not 0 <= col < self.GRID_SIZE:
            raise ValueError(f"row/col out of bounds: ({row}, {col})")
        async with self._lock:
            self.canvas[row][col] = colour
        event = {
            "event": "tile_claimed",
            "row": row,
            "col": col,
            "colour": colour,
            "claimed_by": claimed_by,
        }
        await self._broadcast(event)
        return event

    async def append_form_step(self, step: int, label: str, value: str) -> dict[str, Any]:
        entry = {"step": step, "label": label, "value": value}
        async with self._lock:
            self.form_steps.append(entry)
        event = {"event": "form_step", **entry}
        await self._broadcast(event)
        return event

    async def reset(self) -> None:
        async with self._lock:
            self.canvas = [[None] * self.GRID_SIZE for _ in range(self.GRID_SIZE)]
            self.form_steps = []
        await self._broadcast({"event": "reset"})

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            # Initial snapshot so a fresh subscriber sees current state without
            # waiting for the next mutation.
            yield {"event": "snapshot", **self.snapshot()}
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow subscriber — drop rather than block the writer.
                pass


def _make_app(state: _State) -> Starlette:
    async def get_state(_request: Request) -> JSONResponse:
        return JSONResponse(state.snapshot())

    async def post_claim(request: Request) -> JSONResponse:
        body = await request.json()
        row = int(body["row"])
        col = int(body["col"])
        colour = str(body["colour"])
        claimed_by = str(body.get("claimed_by", "anonymous"))
        try:
            event = await state.claim_tile(row, col, colour, claimed_by)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(event)

    async def post_form_step(request: Request) -> JSONResponse:
        body = await request.json()
        step = int(body["step"])
        label = str(body["label"])
        value = str(body["value"])
        event = await state.append_form_step(step, label, value)
        return JSONResponse(event)

    async def post_reset(_request: Request) -> Response:
        await state.reset()
        return Response(status_code=204)

    async def sse_events(_request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[bytes]:
            async for event in state.subscribe():
                yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    routes = [
        Route("/api/state", get_state, methods=["GET"]),
        Route("/api/claim", post_claim, methods=["POST"]),
        Route("/api/form-step", post_form_step, methods=["POST"]),
        Route("/api/reset", post_reset, methods=["POST"]),
        Route("/api/events", sse_events, methods=["GET"]),
        Mount("/", app=StaticFiles(directory=str(_STATIC_DIR), html=True), name="static"),
    ]
    return Starlette(routes=routes)


class PlaygroundServer:
    """Lifecycle wrapper for tests and recording scripts.

    Usage::

        server = PlaygroundServer()
        await server.start()
        try:
            ...
        finally:
            await server.stop()
    """

    def __init__(self, *, port: int | None = None, host: str = "127.0.0.1") -> None:
        self.port = port if port is not None else _resolve_port()
        self.host = host
        self._state = _State()
        self._config = uvicorn.Config(
            _make_app(self._state),
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(self._config)
        # Uvicorn installs SIGINT/SIGTERM handlers by default, which sys.exits
        # the process when the embedding script gets a signal — we want the
        # caller to control shutdown via PlaygroundServer.stop().
        self._server.install_signal_handlers = lambda: None  # type: ignore[assignment]
        self._task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def start(self) -> None:
        # Uvicorn calls sys.exit(1) on bind failure, which is fatal for the
        # embedding script. Pre-check the port so we can raise a clean error
        # instead. The race window between this check and uvicorn's bind is
        # acceptable for a single-host demo recording.
        import socket

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                # SO_REUSEADDR matches uvicorn's default so a TIME_WAIT socket
                # from a previous run on the same port doesn't fail the probe
                # while staying conservative against an actual LISTEN conflict.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((self.host, self.port))
        except OSError as exc:
            raise RuntimeError(
                f"playground port {self.host}:{self.port} is busy ({exc}); "
                f"set OCTOWRIGHT_PLAYGROUND_PORT to a free port or stop the conflicting process"
            ) from exc

        self._task = asyncio.create_task(self._server.serve())
        # Give uvicorn a moment to bind before the caller starts hitting it.
        # Uvicorn's `started` flag flips inside serve(); poll briefly.
        for _ in range(50):
            if self._server.started:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError(f"playground server did not start within 2.5s on {self.host}:{self.port}")

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except TimeoutError:
                self._task.cancel()


def main() -> int:
    """Run the server in the foreground. Useful for manual development."""
    server = PlaygroundServer()
    print(f"playground listening on {server.url}")

    async def _run() -> None:
        await server.start()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await server.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
