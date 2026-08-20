# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Canary for a Playwright ordering guarantee the SSRF guard depends on.

``ssrf_guard.install_navigation_guard`` registers a CONTEXT route at launch and
validates each redirect hop with its own ``route.fetch()``. A header injector
(``browser_inject_headers``) registers a route later. If the injector did not
run FIRST, the guard's validation hop and the browser's real navigation would
carry different headers -- so on a server that varies its redirect by auth, the
guard would be reasoning about a chain the browser never follows.

That ordering is Playwright's, not ours, and nothing in this repo would notice
if it changed: the header would simply stop reaching the validation hop, quietly.
Hence a canary rather than a unit test.

Measured 2026-08-20 on chromium, firefox and webkit (Playwright 1.62): handlers
run LAST-REGISTERED-FIRST, on the page and on the context alike, so an injector
registered after the guard precedes it.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

pytestmark = pytest.mark.live_browser

_SEEN: list[dict[str, Any]] = []
_LOCK = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        with _LOCK:
            _SEEN.append({"path": self.path, "injected": self.headers.get("X-Canary")})
        body = b"<html>ok</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server():  # type: ignore[no-untyped-def]
    with _LOCK:
        _SEEN.clear()
    # Ephemeral port: never collide with a running service.
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
async def test_a_later_context_route_runs_before_the_guard(kind: str, server: str) -> None:
    """Both the guard's validation fetch and the real navigation must carry the
    injected header. If this fails, the SSRF guard is validating a different
    request than the browser makes."""
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    order: list[str] = []
    guard_saw: dict[str, Any] = {}

    async with async_playwright() as pw:
        try:
            browser = await getattr(pw, kind).launch(headless=True)
        except Exception as exc:  # engine not installed in this environment
            pytest.skip(f"{kind} unavailable: {exc}")
        context = await browser.new_context()
        page = await context.new_page()

        async def guard(route: Any) -> None:
            order.append("guard")
            guard_saw["header"] = (await route.request.all_headers()).get("x-canary")
            await route.fetch(url=route.request.url, max_redirects=0)
            await route.fallback()

        async def injector(route: Any) -> None:
            order.append("injector")
            await route.fallback(headers={**route.request.headers, "X-Canary": "1"})

        await context.route("**/*", guard)  # the SSRF guard, installed at launch
        await context.route("**/*", injector)  # browser_inject_headers, later
        await page.goto(f"{server}/canary")
        await asyncio.sleep(0.1)
        await browser.close()

    with _LOCK:
        rows = [row for row in _SEEN if row["path"].endswith("/canary")]

    assert order[:2] == ["injector", "guard"], f"{kind}: handler order changed"
    assert guard_saw["header"] == "1", f"{kind}: guard validated a request without the injected header"
    assert rows and all(row["injected"] == "1" for row in rows), f"{kind}: header missing on the wire"


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit"])
@pytest.mark.parametrize("mock_first", [True, False])
async def test_a_fulfilling_page_route_shadows_a_context_route_either_way(
    kind: str, mock_first: bool, server: str
) -> None:
    """``mock_route`` is a PAGE route; ``inject_headers`` is a CONTEXT route.

    While both were page routes, last-registered-first decided which won, so a
    mock could only shadow an injector installed before it. Page routes are
    evaluated ahead of context routes, so the mock now wins in BOTH orders and
    the injector never runs at all -- which is why the shadow warning has to
    fire from both install sites rather than only from ``inject_headers``.

    Measured 2026-08-20 on chromium, firefox and webkit (Playwright 1.62).
    """
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    ran: list[str] = []

    async with async_playwright() as pw:
        try:
            browser = await getattr(pw, kind).launch(headless=True)
        except Exception as exc:  # engine not installed in this environment
            pytest.skip(f"{kind} unavailable: {exc}")
        context = await browser.new_context()
        page = await context.new_page()

        async def injector(route: Any) -> None:
            ran.append("context-injector")
            await route.fallback(headers={**route.request.headers, "X-Canary": "1"})

        async def mock(route: Any) -> None:
            ran.append("page-mock")
            await route.fulfill(status=200, content_type="text/html", body="<html>mocked</html>")

        if mock_first:
            await page.route("**/*", mock)
            await context.route("**/*", injector)
        else:
            await context.route("**/*", injector)
            await page.route("**/*", mock)

        # domcontentloaded, not the default load: the body is a fulfilled stub
        # with no subresources, and waiting on webkit's load event here was
        # observed to time out once in ~9 runs for no reason this test is about.
        await page.goto(f"{server}/shadowed", wait_until="domcontentloaded")
        body = await page.content()
        await asyncio.sleep(0.1)
        await browser.close()

    assert ran == ["page-mock"]
    assert "mocked" in body
