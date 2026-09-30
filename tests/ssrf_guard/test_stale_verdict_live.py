# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A page's own navigation that fails late must not cancel the tool navigation that replaced it.

The page starts navigating to ``/fails-late``, whose server holds the request
and then drops the connection, so the guard's fetch of it fails. Before it
does, the tool navigates to ``/slow``. The guard recorded endings per frame,
so the late failure ended the tool's chain: ``navigate`` raised
``NavigationFailedError`` naming ``/fails-late`` and cancelled a ``goto`` that
would have succeeded. ``test_stale_verdict.py`` pins the mechanism.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser

FAIL_AFTER_SECONDS = 3.0
SLOW_SECONDS = 5.0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        srv: Any = self.server
        path = self.path.split("?", 1)[0]
        if path == "/fails-late":
            srv.fails_late_arrived.set()
            time.sleep(FAIL_AFTER_SECONDS)
            self.close_connection = True  # no status line at all: the fetch fails
            return
        if path == "/slow":
            time.sleep(SLOW_SECONDS)
        body = f"<!doctype html><title>{path}</title><h1>{path}</h1>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[Any]:
    srv: Any = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    srv.fails_late_arrived = threading.Event()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def pool(request: pytest.FixtureRequest, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "127.0.0.1")
    browsers = BrowserPool(recordings_dir=tmp_path)
    browsers.kind = request.param
    try:
        yield browsers
    finally:
        await browsers.shutdown()


async def test_a_late_failure_of_the_pages_own_navigation_does_not_fail_the_tools(pool: Any, server: Any) -> None:
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        inst = await pool.launch(kind=pool.kind, headed=False, url=f"{base}/start")
    except Exception as exc:  # engine not installed on this host
        pytest.skip(f"{pool.kind} unavailable: {exc}")
    session = pool.get(inst["instance_id"])
    # Through the session gate, so the launch's markdown capture has finished
    # first: queued behind /fails-late (on chromium it waits for the pending
    # navigation), it would hold ``navigate`` until the fetch had already failed.
    await session.evaluate("location.href = '/fails-late'")
    # The guard is now inside its fetch of /fails-late.
    assert await asyncio.to_thread(server.fails_late_arrived.wait, 10)
    result = await session.navigate(f"{base}/slow")
    assert result["url"] == f"{base}/slow", result
    assert await session.page.title() == "/slow"
