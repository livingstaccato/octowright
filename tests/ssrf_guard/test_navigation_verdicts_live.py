# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Every tool navigation reports the guard's verdict, and says which kind it was.

``test_navigation_settles_live.py`` covers the destination and a refused later
hop. These cover what it left open:

* a popup's FIRST request has no frame yet (Playwright raises on
  ``request.frame`` until the popup page exists -- measured on all three
  engines), so a refusal of its first redirect was dropped and ``open_url``
  reported success on the error page (chromium, webkit) or waited out its whole
  timeout (firefox);
* a hop whose fetch FAILED (connection refused) was reported as a policy
  refusal -- an ``InvalidRequestError``, the caller's-own-input type;
* a redirected popup waited for the destination's ``load``, so one hanging
  image kept ``open_url`` waiting its whole timeout where an unredirected popup
  returns at ``domcontentloaded``;
* crash recovery onto a refused last URL raised, leaving the dead page as the
  session's page and the new one orphaned.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from octowright import ssrf_guard
from octowright.browser_pool import crash_recovery, incidents
from octowright.browser_pool.pool import BrowserPool
from octowright.request_errors import InvalidRequestError
from octowright.session import core_ops_mixin
from octowright.ssrf_guard import NavigationFailedError

pytestmark = pytest.mark.live_browser

HANG_SECONDS = 60


def _closed_port() -> int:
    """A loopback port nothing listens on, so a fetch to it is refused at once."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


#: Pages that close their own window, as an OAuth popup does once it has
#: handed its result back to the opener.
_SELF_CLOSING = {
    "/closes-on-load": b"<!doctype html><title>done</title><script>addEventListener('load', () => window.close())</script>",
    "/closes-while-parsing": b"<!doctype html><title>done</title><script>window.close()</script>",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        srv: Any = self.server
        port = srv.server_address[1]
        path = self.path.split("?", 1)[0]
        if path == "/hang":
            srv.hang_release.wait(HANG_SECONDS)
            return
        redirects = {
            "/to-metadata": "http://169.254.169.254/latest/meta-data/",
            "/later-metadata": "/to-metadata",
            "/to-closed": f"http://127.0.0.1:{srv.closed_port}/x",
            "/later-closed": "/to-closed",
            "/to-slow-page": "/slow-page",
            "/later-blocked": "/blocked",
            "/blocked": f"http://localhost:{port}/secret",
            "/to-closes-on-load": "/closes-on-load",
            "/to-closes-while-parsing": "/closes-while-parsing",
            "/to-hang": "/hang",
        }
        if path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in _SELF_CLOSING:
            body = _SELF_CLOSING[path]
        elif path == "/slow-page":
            # DOMContentLoaded does not wait for an image; load does.
            body = b'<!doctype html><title>Slow</title><h1>slow</h1><img src="/hang">'
        else:
            body = f"<!doctype html><title>{path}</title><h1>{path}</h1>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def base() -> Iterator[str]:
    srv: Any = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    srv.closed_port = _closed_port()
    srv.hang_release = threading.Event()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.hang_release.set()
    srv.shutdown()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def pool(request: pytest.FixtureRequest, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "127.0.0.1")
    # Short enough that a wait running out its budget fails the test rather
    # than the per-test timeout.
    monkeypatch.setattr(core_ops_mixin, "DEFAULT_NAV_TIMEOUT_MS", 15_000)
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


@pytest.mark.parametrize("path", ["/to-metadata", "/later-metadata"])
@pytest.mark.parametrize("target", ["tab", "window"])
async def test_a_refused_redirect_fails_open_url_promptly(pool: Any, base: str, target: str, path: str) -> None:
    """``/to-metadata`` is refused on a popup's first request, which has no frame to record it on."""
    session = await _launch(pool, f"{base}/start")
    started = time.monotonic()
    result = await session.open_url(f"{base}{path}", target=target)
    assert result["ok"] is False, result
    assert "169.254.169.254" in result["error"], result
    assert time.monotonic() - started < 10, "the refusal did not end the wait"


@pytest.mark.parametrize("path", ["/to-closed", "/later-closed"])
async def test_a_failed_fetch_is_a_navigation_error_not_a_refusal(pool: Any, base: str, path: str) -> None:
    session = await _launch(pool, f"{base}/start")
    with pytest.raises(NavigationFailedError) as info:
        await session.navigate(f"{base}{path}")
    assert not isinstance(info.value, InvalidRequestError)


@pytest.mark.parametrize("target", ["tab", "window"])
async def test_a_failed_fetch_fails_open_url_promptly(pool: Any, base: str, target: str) -> None:
    session = await _launch(pool, f"{base}/start")
    started = time.monotonic()
    result = await session.open_url(f"{base}/later-closed", target=target)
    assert result["ok"] is False and "failed" in result["error"], result
    assert time.monotonic() - started < 10, "the failure did not end the wait"


@pytest.mark.parametrize("path", ["/slow-page", "/to-slow-page"])
async def test_a_popup_returns_at_domcontentloaded_redirected_or_not(pool: Any, base: str, path: str) -> None:
    """A hanging image must not hold a redirected popup until the navigation timeout."""
    session = await _launch(pool, f"{base}/start")
    started = time.monotonic()
    result = await session.open_url(f"{base}{path}", target="window")
    assert result.get("error") is None, result
    assert result["url"] == f"{base}/slow-page"
    assert time.monotonic() - started < 10, "the popup waited for load, not domcontentloaded"


@pytest.mark.parametrize("path", ["/closes-on-load", "/to-closes-on-load"])
async def test_a_popup_that_closes_itself_on_load_returns_at_once(pool: Any, base: str, path: str) -> None:
    """The redirect probe read the closed page as "still navigating" and waited out the whole budget."""
    session = await _launch(pool, f"{base}/start")
    started = time.monotonic()
    result = await session.open_url(f"{base}{path}", target="window")
    assert time.monotonic() - started < 10, "a closed popup was waited on until the navigation timeout"
    # As with the policy off: the popup reached its destination before it closed.
    assert result.get("error") is None, result
    assert result["url"] == f"{base}/closes-on-load"


@pytest.mark.parametrize("path", ["/closes-while-parsing", "/to-closes-while-parsing"])
async def test_a_popup_that_closes_while_parsing_returns_at_once(pool: Any, base: str, path: str) -> None:
    """Whether its DOMContentLoaded fires first differs by engine (as with the policy off); the wait must not."""
    session = await _launch(pool, f"{base}/start")
    started = time.monotonic()
    result = await session.open_url(f"{base}{path}", target="window")
    assert time.monotonic() - started < 10, "a closed popup was waited on until the navigation timeout"
    assert result.get("error") is None or "closed" in result["error"], result


@pytest.mark.parametrize(("path", "on_stub"), [("/to-hang", True), ("/start", False)])
async def test_a_closed_popup_still_says_whether_it_was_on_the_stub(
    pool: Any, base: str, path: str, on_stub: bool
) -> None:
    """What ``_settle_guarded_popup`` asks at a close its own probe never saw coming.

    ``/to-hang``'s destination never answers, so the popup stays on the stub.
    The popup's first request has no frame while it is served; the record must
    still resolve to the popup's frame, and after the page has closed.
    """
    session = await _launch(pool, f"{base}/start")
    async with session.page.expect_popup() as info:
        await session.page.evaluate("u => { window.open(u, '_blank', 'popup') }", f"{base}{path}")
    popup = await info.value
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if await popup.evaluate(ssrf_guard.IS_CLIENT_REDIRECT_JS) is on_stub and popup.url != "about:blank":
                break
        except Exception:  # the document is being replaced under the probe: ask again
            pass
        await asyncio.sleep(0.1)
    assert ssrf_guard.served_client_redirect_last(popup.main_frame) is on_stub
    await popup.close()
    assert ssrf_guard.served_client_redirect_last(popup.main_frame) is on_stub


async def test_crash_recovery_onto_a_refused_url_recovers_and_reports_it(pool: Any, base: str) -> None:
    session = await _launch(pool, f"{base}/start")
    incidents.reset()
    dead = session.page
    recovered = await crash_recovery._recover(session, dead, 15_000, f"{base}/later-blocked")
    assert recovered is True
    assert session.page is not dead and not session.page.is_closed()
    assert dead not in session.pages and session.page in session.pages
    assert session._crashed is False
    (incident,) = incidents.recent(category=incidents.CATEGORY_RENDERER_CRASH)
    assert incident["outcome"] == "recovered"
    assert "localhost" in incident["navigation_error"]
    # The session is usable: the next navigation works.
    await session.navigate(f"{base}/after")
    assert await session.page.title() == "/after"
