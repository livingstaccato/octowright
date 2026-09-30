# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A subresource's redirect is NOT re-checked: the measured, documented limit.

The guard checks a subresource's first URL only. Playwright never calls the
route handler again for a redirect hop, on any engine, after ``fallback()``
or ``continue_()`` (measured), so ``<img src=public/r>`` answering
``302 -> private`` reaches the private host on firefox and webkit: a blind
SSRF. Chromium refuses it today, but not because of this guard -- its Local
Network Access check sees a guard-fulfilled page as public and headless
denies the permission ("blocked by CORS policy: Permission was denied").

Closing it would mean the guard fetching every subresource itself and
fulfilling the result, and measured on all three engines that breaks what the
browser enforces: a same-origin URL redirecting cross-origin became readable
by the page, a streamed response delivered nothing until it ended, and
``credentials: 'omit'`` still sent cookies. See ``ssrf_guard``'s docstring.

These are canaries, not endorsements: firefox and webkit are strict xfails, so
the day an engine stops following the hop -- or Chromium's check changes --
this fails and the documentation has to be updated with it.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tests.ssrf_guard import test_subresource_and_toctou_live as _served

pytestmark = pytest.mark.live_browser

# A guarded context on each engine.
context = _served.context

_FOLLOWED = {"firefox", "webkit"}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        srv: Any = self.server
        path = self.path.split("?", 1)[0]
        srv.served.append(path)
        if path.startswith("/r/"):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{srv.server_address[1]}/private{path}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<title>page</title>"
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
    srv.served = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


async def test_a_subresource_redirect_to_a_private_host_is_not_followed(
    request: pytest.FixtureRequest, server: Any, context: Any
) -> None:
    engine = request.node.callspec.params["context"]
    if engine in _FOLLOWED:
        request.applymarker(
            pytest.mark.xfail(strict=True, reason=f"{engine} follows a subresource redirect unchecked (documented)")
        )
    base = f"http://127.0.0.1:{server.server_address[1]}"
    page = await context.new_page()
    await page.goto(f"{base}/page")
    await page.evaluate(
        "(b) => new Promise(r => { const i = new Image(); i.onload = i.onerror = () => r(); i.src = b + '/r/img'; })",
        base,
    )
    await page.evaluate("(b) => fetch(b + '/r/fetch', {mode: 'no-cors'}).catch(() => null)", base)
    reached = [p for p in server.served if p.startswith("/private/")]
    assert reached == [], f"{engine} followed the redirect to the private host: {reached}"
