# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""An open streaming connection must not hold the leader's shutdown hostage.

uvicorn turns SIGTERM into a graceful shutdown, but by default waits with no
bound for every open connection to finish. Every connected follower holds a
never-ending SSE stream (``/api/mcp-events``, the ``/mcp`` GET stream), so a
daemon with any client attached never left ``serve()``: SIGTERM -- from
``octowright restart``, systemd, a container stop -- did nothing until the
sender escalated to SIGKILL, which skips browser/plugin-pool teardown and the
leader's lock removal. Reproduced against an isolated ``--daemon-mode``
process: exits in ~0.5s on SIGTERM with no client, never with one SSE client
attached, and exits the moment that client disconnects.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from octowright.http import lifespan as _http_lifespan


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _forever(_request: Request) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        yield b": ready\n\n"
        while True:
            await asyncio.sleep(3600)
            yield b": ping\n\n"

    return StreamingResponse(body(), media_type="text/event-stream")


@pytest.mark.anyio
async def test_shutdown_completes_with_a_follower_stream_open(monkeypatch: pytest.MonkeyPatch) -> None:
    servers: list[Any] = []

    class _CapturingServer(uvicorn.Server):
        def __init__(self, config: uvicorn.Config) -> None:
            super().__init__(config)
            servers.append(self)

    monkeypatch.setattr(uvicorn, "Server", _CapturingServer)
    monkeypatch.setattr(
        _http_lifespan,
        "build_app",
        lambda **_k: Starlette(routes=[Route("/api/mcp-events", _forever)]),
    )
    port = _free_port()
    bound = asyncio.Event()
    serve = asyncio.create_task(
        _http_lifespan.serve_app(host="127.0.0.1", port=port, retries=0, on_bound=lambda _h, _p: bound.set())
    )
    try:
        await asyncio.wait_for(bound.wait(), 5)
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5)) as client:
            for _ in range(50):
                try:
                    stream_cm = client.stream("GET", f"http://127.0.0.1:{port}/api/mcp-events")
                    response = await stream_cm.__aenter__()
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
            lines = response.aiter_lines()
            assert (await asyncio.wait_for(lines.__anext__(), 5)) == ": ready"

            servers[0].should_exit = True  # what uvicorn's SIGTERM handler does
            await asyncio.wait_for(asyncio.shield(serve), 10)
            await stream_cm.__aexit__(None, None, None)
    finally:
        if not serve.done():
            serve.cancel()
            with pytest.raises(asyncio.CancelledError):
                await serve
