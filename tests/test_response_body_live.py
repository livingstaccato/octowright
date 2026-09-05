# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Failed-response bodies, against a real browser and a real server.

The unit tests pin the scoping rules against fakes. This pins the part no fake
can: that Playwright actually hands the body over from a page event handler,
and that it is captured EAGERLY. Measured on Chromium, a body requested after
the page has navigated away fails with ``Protocol error
(Network.getResponseBody): No resource with given identifier`` -- so a lazy
read at tool-call time would return nothing exactly when someone is
investigating a failure, and only a live test can catch that regressing.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser

_CONFLICT_BODY = b'{"detail": "component_allocation_required"}'


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/conflict":
            body, status, ctype = _CONFLICT_BODY, 409, "application/json"
        elif self.path == "/huge":
            body, status, ctype = b"E" * 9000, 500, "text/plain"
        else:
            body, status, ctype = b"<html><body>ok</body></html>", 200, "text/html"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server():  # type: ignore[no-untyped-def]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


async def _rows_after_fetches(pool: BrowserPool, server: str) -> list[dict[str, Any]]:
    info = await pool.launch(kind="chromium", url=f"{server}/", headed=False)
    session = pool.get(info["instance_id"])
    await session.evaluate("fetch('/conflict').catch(()=>{})")
    await session.evaluate("fetch('/huge').catch(()=>{})")
    # The body read is a detached task; drain it rather than sleeping.
    await session._drain_background_tasks()
    return session.get_network_requests(limit=None)["requests"]


@pytest.mark.anyio
async def test_failed_response_bodies_are_captured_from_a_real_browser(server: str) -> None:
    pool = BrowserPool()
    try:
        rows = await _rows_after_fetches(pool, server)
    finally:
        await pool.close_all(force=True)

    by_path = {row["url"].rsplit("/", 1)[-1]: row for row in rows}

    # The refusal reason, verbatim -- the field report's entire diagnosis.
    assert by_path["conflict"]["body"] == _CONFLICT_BODY.decode()
    assert by_path["conflict"]["body_truncated"] is False

    # Capped, and honest about it.
    assert len(by_path["huge"]["body"]) == 2048
    assert by_path["huge"]["body_truncated"] is True

    # A successful response never pays for a body.
    assert all("body" not in row for row in rows if 200 <= (row.get("status") or 0) < 300)
