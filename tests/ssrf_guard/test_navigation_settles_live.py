# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A tool navigation under ``block-private`` returns on the page it was sent to.

The guard answers a redirect with a client-redirect document, and ``goto``
resolves when THAT document loads -- so ``navigate`` returned, and the next
step ran, on a blank stub rather than the destination: the title was empty,
``expect_url`` saw the first URL, and ``expect_no_text`` scanned the stub and
passed on a page the forbidden text was really drawn on. Every check here runs
straight after the navigation, with no ``wait_for_url`` in between -- that is
the whole point.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.macros import execution

pytestmark = pytest.mark.live_browser

SECRET = "ACCOUNT-NUMBER-4242"  # pragma: allowlist secret (page body marker, not a credential)
ACCOUNT = f"<!doctype html><title>Account</title><h1>{SECRET}</h1>".encode()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        srv: Any = self.server
        port = srv.server_address[1]
        path = self.path.split("?", 1)[0]
        if path == "/once":
            # A page the first visit renders and every later visit redirects,
            # so going back to it is a redirecting navigation.
            srv.once += 1
            if srv.once > 1:
                self._redirect("/acct/")
                return
        redirects = {
            "/acct": "/acct/",
            "/two": "/acct",
            "/later-blocked": "/blocked",
            "/blocked": f"http://localhost:{port}/secret",
        }
        if path in redirects:
            self._redirect(redirects[path])
            return
        body = ACCOUNT if path == "/acct/" else f"<!doctype html><title>{path}</title><h1>{path}</h1>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Cache-Control", "no-store")  # keep history navigations off the bfcache
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def base() -> Iterator[str]:
    srv: Any = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.once = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
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


async def _launch(pool: Any, url: str) -> Any:
    try:
        inst = await pool.launch(kind=pool.kind, headed=False, url=url)
    except Exception as exc:  # engine not installed on this host
        pytest.skip(f"{pool.kind} unavailable: {exc}")
    return pool.get(inst["instance_id"])


async def _assert_on_account(session: Any, page: Any, base: str) -> None:
    assert page.url == f"{base}/acct/"
    assert await page.title() == "Account"
    assert SECRET in await page.inner_text("h1")


async def test_navigate_returns_on_the_destination(pool: Any, base: str) -> None:
    session = await _launch(pool, f"{base}/start")
    result = await session.navigate(f"{base}/two")
    assert result["title"] == "Account"
    await _assert_on_account(session, session.page, base)
    assert await session.expect_url(f"{base}/acct/", mode="equals")
    with pytest.raises(RuntimeError):
        await session.expect_no_text(SECRET)


async def test_a_replayed_navigate_is_followed_by_a_check_of_the_destination(
    pool: Any, base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await _launch(pool, f"{base}/start")
    actions = [{"action": "navigate", "url": f"{base}/acct"}, {"action": "expect_no_text", "text": SECRET}]
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    with pytest.raises(RuntimeError):
        await execution.run_macro(session, "leak")


async def test_launch_returns_on_the_destination(pool: Any, base: str) -> None:
    session = await _launch(pool, f"{base}/two")
    await _assert_on_account(session, session.page, base)


async def test_navigate_back_returns_on_the_destination(pool: Any, base: str) -> None:
    session = await _launch(pool, f"{base}/once")
    await session.navigate(f"{base}/start")
    result = await session.navigate_back()
    assert result["title"] == "Account"
    await _assert_on_account(session, session.page, base)


@pytest.mark.parametrize("target", ["tab", "window"])
async def test_open_url_returns_on_the_destination(pool: Any, base: str, target: str) -> None:
    session = await _launch(pool, f"{base}/start")
    result = await session.open_url(f"{base}/two", target=target)
    assert result.get("error") is None, result
    new_page = session.pages[result["page_index"]]
    await _assert_on_account(session, new_page, base)


async def test_a_refused_later_hop_fails_the_navigation(pool: Any, base: str) -> None:
    """``goto`` already follows the client redirect to the refused hop and fails there."""
    session = await _launch(pool, f"{base}/start")
    with pytest.raises(Exception):
        await session.navigate(f"{base}/later-blocked")


async def test_a_refused_later_hop_fails_a_popup_promptly(pool: Any, base: str) -> None:
    """It returned ok on the stub the refused hop left behind; waiting for its load never ends."""
    session = await _launch(pool, f"{base}/start")
    started = time.monotonic()
    result = await session.open_url(f"{base}/later-blocked", target="window")
    assert result["ok"] is False and "localhost" in result["error"], result
    assert time.monotonic() - started < 10, "the refusal did not end the wait"
