# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A redirect to a non-HTTP(S) scheme must not become a client redirect.

The guard answers a redirect with a document that ``location.replace``-s
itself to the ``Location``. The policy has no host to check for
``javascript:``, so before the scheme check a ``302 Location: javascript:...``
became that document and its script ran as the redirecting page (measured:
``document.title`` was set on chromium, firefox and webkit). A browser left to
itself refuses every such redirect as a network error.

``data:``, ``blob:`` and ``file:`` targets did not execute (the engines refuse
a top-level navigation to them from an http page), but the guard still served
a document to them; all four are now refused the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tests.ssrf_guard import test_subresource_and_toctou_live as _served

pytestmark = pytest.mark.live_browser

# A guarded context on each engine.
context = _served.context

_PAYLOAD = "document.title='PWNED';window.__pwned=1"
_TARGETS = {
    "/js": f"javascript:{_PAYLOAD}",
    "/data": f"data:text/html,<script>parent.__pwned=1;{_PAYLOAD}</script>",
    "/blob": "blob:http://127.0.0.1/00000000-0000-0000-0000-000000000000",
    "/file": "file:///etc/hostname",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        location = _TARGETS.get(self.path)
        if location is not None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<title>start</title><h1>start</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def base() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.parametrize("path", sorted(_TARGETS))
async def test_a_redirect_to_a_non_http_scheme_is_refused(base: str, context: Any, path: str) -> None:
    page = await context.new_page()
    await page.goto(f"{base}/start")
    served: list[str] = []
    page.on("response", lambda r: served.append(r.url))
    refused = False
    try:
        await page.goto(f"{base}{path}", timeout=10_000)
    except Exception:  # each engine names the abort differently
        refused = True
    await asyncio.sleep(0.5)  # a served client redirect would have run by now
    for frame in page.frames:
        seen: list[Any] = []
        with contextlib.suppress(Exception):  # an error page may refuse evaluation
            seen = await frame.evaluate("() => [window.__pwned || null, document.title]")
        assert seen[:1] in ([], [None]) and "PWNED" not in seen, (frame.url, seen)
    assert f"{base}{path}" not in served, "the guard served a document for the redirect"
    assert refused, "the navigation to a refused redirect reported success"
