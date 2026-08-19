# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A redirect must be re-checked against the SSRF policy.

Pre-flight checking only the requested URL let a public first hop bounce the
browser into a private/metadata host, whose body the read tools then return.
"""

from __future__ import annotations

import contextlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from octowright.ssrf_guard import install_navigation_guard

pytestmark = pytest.mark.live_browser

SECRET = "INTERNAL-METADATA-CONTENT"  # pragma: allowlist secret (page body marker, not a credential)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.served.append(self.path)  # type: ignore[attr-defined]
        if self.path.startswith("/redir"):
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)  # type: ignore[attr-defined]
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"<h1>{SECRET}</h1>".encode())

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def server():
    """Serve /redir and /secret, recording every path actually requested.

    The redirect points at ``localhost`` while the test allowlists
    ``127.0.0.1``: same machine, but a host the policy refuses, so the two
    hops are distinguishable.
    """
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    srv.served = []  # type: ignore[attr-defined]
    srv.redirect_to = f"http://localhost:{port}/secret"  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture
async def context():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        yield ctx
        await browser.close()


async def test_redirect_to_a_blocked_host_never_reaches_it(
    monkeypatch: pytest.MonkeyPatch, server: Any, context: Any
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    # The first hop stands in for a public redirector the policy allows.
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "127.0.0.1")
    await install_navigation_guard(context)
    page = await context.new_page()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    # goto may resolve with the 302 itself rather than raising -- the abort
    # lands on the redirect hop, which is a separate request. The proof is
    # server-side either way.
    with contextlib.suppress(Exception):
        await page.goto(f"{base}/redir")

    # Server-side is the proof that matters: the blocked host was never
    # contacted. (page.content() is unreliable here -- the aborted navigation
    # leaves the page mid-transition.)
    assert "/redir" in server.served
    assert "/secret" not in server.served


async def test_allowed_navigation_still_loads(monkeypatch: pytest.MonkeyPatch, server: Any, context: Any) -> None:
    """The guard must not break ordinary browsing under the same policy."""
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "127.0.0.1")
    await install_navigation_guard(context)
    page = await context.new_page()
    await page.goto(f"http://127.0.0.1:{server.server_address[1]}/secret")
    assert SECRET in await page.content()


async def test_policy_off_installs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default deployments keep an uninstrumented context."""
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    calls: list[Any] = []

    class _Ctx:
        async def route(self, *args: Any) -> None:
            calls.append(args)

    await install_navigation_guard(_Ctx())
    assert calls == []


async def test_policy_on_registers_a_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    calls: list[Any] = []

    class _Ctx:
        async def route(self, *args: Any) -> None:
            calls.append(args)

    await install_navigation_guard(_Ctx())
    assert len(calls) == 1
