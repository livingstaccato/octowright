# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``block-private`` covers every request, and the browser gets what was validated.

Two gaps, both measured on all three engines:

* **Subresources were not checked at all.** A page on an allowed host could
  ``fetch()`` a private one, load it as an image, or open a WebSocket to it.
* **The validated response was thrown away.** The guard walked the chain with
  ``route.fetch`` and then ``route.fallback()``-ed, so the browser fetched
  every hop again -- and a server that answers "200" to the first request and
  "302 -> private" to the second got its redirect followed.

The fixture serves on ``127.0.0.1`` and allowlists it, so it stands in for a
public host; ``localhost`` is the same machine under a name the policy refuses,
which makes a blocked request distinguishable server-side.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from octowright.ssrf_guard import install_navigation_guard

pytestmark = pytest.mark.live_browser

SECRET = "INTERNAL-METADATA-CONTENT"  # pragma: allowlist secret (page body marker, not a credential)
# A 1x1 transparent GIF.
GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        srv: Any = self.server
        path = self.path.split("?", 1)[0]
        srv.served.append(path)
        if path == "/flip":
            # Safe to the first request, a redirect to the refused host after.
            srv.flips += 1
            if srv.flips > 1:
                self._redirect(f"http://localhost:{srv.server_address[1]}/secret")
                return
            self._send(b"<h1>safe</h1>", "text/html")
            return
        hops = {"/d1/a": "/d2/b", "/d2/b": "/d3/c", "/loop": "/loop", "/to-flip": "/flip"}
        if path in hops:
            self._redirect(hops[path])
            return
        if path == "/img.gif":
            self._send(GIF, "image/gif")
            return
        if path == "/d3/c":
            self._send(b'<a id="l" href="rel">rel</a><h1>final</h1>', "text/html")
            return
        self._send(f"<h1>{SECRET}</h1>".encode(), "text/html")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[Any]:
    srv: Any = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.served = []
    srv.flips = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def context(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
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


def _base(server: Any) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


async def test_a_private_fetch_is_aborted_and_never_reaches_the_host(server: Any, context: Any) -> None:
    page = await context.new_page()
    await page.goto(f"{_base(server)}/page")
    port = server.server_address[1]
    outcome = await page.evaluate(
        "(u) => fetch(u).then(r => 'status ' + r.status, e => 'rejected ' + e.name)",
        f"http://localhost:{port}/secret",
    )
    assert outcome.startswith("rejected"), outcome
    assert "/secret" not in server.served


async def test_a_private_image_and_websocket_are_refused(server: Any, context: Any) -> None:
    page = await context.new_page()
    await page.goto(f"{_base(server)}/page")
    port = server.server_address[1]
    image = await page.evaluate(
        "(u) => new Promise(r => { const i = new Image(); i.onload = () => r('loaded');"
        " i.onerror = () => r('error'); i.src = u; })",
        f"http://localhost:{port}/secret-image",
    )
    assert image == "error"
    socket = await page.evaluate(
        "(u) => new Promise(r => { const w = new WebSocket(u); w.onopen = () => r('open');"
        " w.onclose = () => r('closed'); setTimeout(() => r('timeout'), 5000); })",
        f"ws://localhost:{port}/secret-socket",
    )
    assert socket == "closed"
    assert "/secret-image" not in server.served
    assert "/secret-socket" not in server.served


async def test_ordinary_subresources_still_load(server: Any, context: Any) -> None:
    page = await context.new_page()
    await page.goto(f"{_base(server)}/page")
    width = await page.evaluate(
        "(u) => new Promise(r => { const i = new Image(); i.onload = () => r(i.naturalWidth);"
        " i.onerror = () => r(-1); i.src = u; })",
        f"{_base(server)}/img.gif",
    )
    assert width == 1
    text = await page.evaluate("(u) => fetch(u).then(r => r.text())", f"{_base(server)}/page")
    assert SECRET in text


async def test_a_server_that_changes_its_answer_gets_the_validated_one(server: Any, context: Any) -> None:
    """The browser must be served the response the guard checked, not a second one."""
    page = await context.new_page()
    with contextlib.suppress(Exception):
        await page.goto(f"{_base(server)}/flip")
    await asyncio.sleep(0.5)
    assert "/secret" not in server.served, server.served
    assert server.served.count("/flip") == 1, server.served
    assert "safe" in await page.content()


async def test_a_changed_answer_mid_chain_is_not_followed(server: Any, context: Any) -> None:
    page = await context.new_page()
    with contextlib.suppress(Exception):
        await page.goto(f"{_base(server)}/to-flip")
    await asyncio.sleep(0.5)
    assert "/secret" not in server.served, server.served
    assert server.served.count("/flip") == 1, server.served


async def test_a_redirect_chain_lands_on_its_real_url(server: Any, context: Any) -> None:
    """Why fallback was used: the page must end at the final URL, links relative to it."""
    page = await context.new_page()
    await page.goto(f"{_base(server)}/d1/a")
    await page.wait_for_url(f"{_base(server)}/d3/c", timeout=10_000)
    assert "final" in await page.content()
    href = await page.evaluate("() => document.getElementById('l').href")
    assert href == f"{_base(server)}/d3/rel"
    # Each hop fetched exactly once, by the guard.
    assert [p for p in server.served if p.startswith("/d")] == ["/d1/a", "/d2/b", "/d3/c"]


async def test_a_redirect_loop_is_bounded(server: Any, context: Any) -> None:
    page = await context.new_page()
    with contextlib.suppress(Exception):
        await page.goto(f"{_base(server)}/loop", timeout=15_000)
    await asyncio.sleep(1.0)
    assert 0 < server.served.count("/loop") <= 21, server.served.count("/loop")
