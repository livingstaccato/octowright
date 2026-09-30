# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A form POST that redirects must be chain-checked -- and submitted once.

The guard used to ``fallback()`` every non-GET navigation, so ``POST -> 303 ->
metadata`` reached the private host unchecked: the browser converts the
redirect to a GET and follows it inside the network stack, where Playwright
does not call the route handler again.

Same model as ``test_redirect_hops_live``: ``127.0.0.1`` is allowlisted and
stands in for a public host, ``localhost`` is the same server under a name the
policy refuses, so which hops the server saw tells the two apart.
"""

from __future__ import annotations

import contextlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from octowright.ssrf_guard import install_navigation_guard

pytestmark = pytest.mark.live_browser

SECRET = "INTERNAL-METADATA-CONTENT"  # pragma: allowlist secret (page body marker, not a credential)
DONE = "SUBMISSION-ACCEPTED"


class _Handler(BaseHTTPRequestHandler):
    def _html(self, body: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self) -> None:
        self.server.served.append(("GET", self.path))  # type: ignore[attr-defined]
        if self.path == "/form":
            self._html('<form method="post" action="/submit"><input name="q" value="1"><button>go</button></form>')
            return
        self._html(f"<h1>{DONE if self.path == '/done' else SECRET}</h1>")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.served.append(("POST", self.path))  # type: ignore[attr-defined]
        self.send_response(303)
        self.send_header("Location", self.server.redirect_to)  # type: ignore[attr-defined]
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.served = []  # type: ignore[attr-defined]
    srv.redirect_to = ""  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def context(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    from playwright.async_api import async_playwright

    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "127.0.0.1")
    async with async_playwright() as pw:
        try:
            browser = await getattr(pw, request.param).launch(headless=True)
        except Exception as exc:  # engine not installed on this host
            pytest.skip(f"{request.param} unavailable: {exc}")
        ctx = await browser.new_context()
        await install_navigation_guard(ctx)
        yield ctx
        await browser.close()


async def _submit(context: Any, base: str) -> Any:
    page = await context.new_page()
    await page.goto(f"{base}/form")
    # The abort lands on the POST itself, so the click's navigation may fail;
    # the server's log is the proof either way.
    with contextlib.suppress(Exception):
        async with page.expect_navigation(timeout=10_000):
            await page.click("button")
    return page


async def test_post_redirect_to_a_blocked_host_never_reaches_it(server: Any, context: Any) -> None:
    port = server.server_address[1]
    server.redirect_to = f"http://localhost:{port}/secret"
    await _submit(context, f"http://127.0.0.1:{port}")
    assert ("POST", "/submit") in server.served
    assert ("GET", "/secret") not in server.served


async def test_post_redirect_to_an_allowed_host_loads_and_submits_once(server: Any, context: Any) -> None:
    port = server.server_address[1]
    server.redirect_to = f"http://127.0.0.1:{port}/done"
    page = await _submit(context, f"http://127.0.0.1:{port}")
    # WebKit cannot be handed the 303 itself and gets a client-side redirect
    # to the validated target (see ssrf_guard), so wait for the URL to settle.
    await page.wait_for_url(f"http://127.0.0.1:{port}/done", timeout=10_000)
    assert DONE in await page.content()
    assert page.url == f"http://127.0.0.1:{port}/done"
    assert server.served.count(("POST", "/submit")) == 1, server.served
