# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Websocket observation against a real browser and a real socket server.

The unit tests drive the handler with a fake that emits payloads the way
playwright-python documents. This pins the part no fake can: that the real
binding emits what we think it emits. That distinction is the whole reason
this feature shipped empty -- the payload was read as ``frame.payload``, which
is Node's shape, and every existing test asserted on row structure rather than
on content, so nothing caught it.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
import websockets

from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser


def _page_for(ws_port: int) -> bytes:
    return f"""<html><body><script>
const ws = new WebSocket("ws://127.0.0.1:{ws_port}/ws");
ws.onopen = () => {{ ws.send("hello-from-page"); }};
</script></body></html>""".encode()


def _handler_for(page: bytes) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *_args: Any) -> None:
            pass

    return _Handler


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def serve_page():  # type: ignore[no-untyped-def]
    """Serve one page on an EPHEMERAL port, like every other live server test.

    Fixed ports (this file shipped with 8894/8895) turn any local service, a
    leftover process, or a second concurrent run into ``Address already in
    use`` -- a failure that says nothing about the code under test. The socket
    port has to be discovered before the page can be written, since the page
    hardcodes it, so the page is built here rather than at import time.
    """
    started: list[ThreadingHTTPServer] = []

    def start(page: bytes) -> str:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(page))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}/"

    try:
        yield start
    finally:
        for srv in started:
            # ``shutdown`` only stops the serve_forever loop; without
            # ``server_close`` the listening socket and ThreadingHTTPServer's
            # non-daemon request threads survive to process exit, so each
            # ``start`` would leak one fd for the rest of the session.
            srv.shutdown()
            srv.server_close()


@pytest.mark.anyio
async def test_frames_are_observable_through_the_session(serve_page: Any) -> None:
    async def handler(conn: Any) -> None:
        async for message in conn:
            await conn.send(f"echo:{message}")

    server = await websockets.serve(handler, "127.0.0.1", 0)
    page_url = serve_page(_page_for(server.sockets[0].getsockname()[1]))
    pool = BrowserPool()
    try:
        info = await pool.launch(kind="chromium", url=page_url, headed=False)
        session = pool.get(info["instance_id"])
        # The page opens the socket on load; wait for the round trip rather
        # than a fixed sleep.
        for _ in range(50):
            if session.get_websocket_summary()["open_count"]:
                messages = session.get_websocket_messages()["messages"]
                if any(m["direction"] == "received" for m in messages):
                    break
            await asyncio.sleep(0.1)

        summary = session.get_websocket_summary()
        assert summary["open_count"] == 1
        assert summary["open"][0]["url"].endswith("/ws")

        messages = session.get_websocket_messages(include_payloads=True)["messages"]
        sent = [m for m in messages if m["direction"] == "sent"]
        received = [m for m in messages if m["direction"] == "received"]

        # The payloads themselves -- the regression this feature shipped with.
        assert sent[0]["payload_text"] == "hello-from-page"
        assert received[0]["payload_text"] == "echo:hello-from-page"
        assert sent[0]["size"] == len("hello-from-page")
    finally:
        await pool.close_all(force=True)
        # Awaited, not just closed: an unawaited server is torn down at GC
        # after the loop has gone, which surfaces as "Event loop is closed"
        # and fails the run even though the test itself passed.
        server.close()
        await server.wait_closed()
