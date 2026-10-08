# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What ``response.request.redirected_from`` reports for a navigation redirect.

A live test once dropped an assertion on it after WebKit reported ``None`` for
an engine-followed navigation redirect. Re-measured on Playwright 1.63 it does
not reproduce: headless chromium, firefox and webkit all link the final
response's request to the 3xx hop -- for ``goto`` through 301/302/303/307/308
(with and without a body), a link click, a form POST answered 303 and a
``location.href`` assignment, with and without a context/page route, launch
or page-level extra headers, and a cross-origin hop. No Octowright code reads
``redirected_from``: the redirect chain on network rows comes from the
guard's tag (``ssrf_guard.client_redirect_of``), and crash recovery decides
``recovered_elsewhere`` from where the page is.

Pinned here on all three engines, both sides of the line the docs draw:

* a session with no guard route keeps the engine's chain, so a client reading
  ``redirected_from`` gets the 3xx hop;
* a session whose navigations go through the guard's single-fetch path (here
  because it carries a URL-scoped injected header, policy off) does not: each
  hop is its own navigation, so the final request was redirected from nothing.
  The real 3xx stays on the hop's network row (``served_as: "client_redirect"``).

If an engine starts dropping the link again, the first test fails on it.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool import BrowserPool
from tests.test_engine_matrix_live import _configure_runtime_paths, _maybe_skip_live_engine

pytestmark = pytest.mark.live_browser

ENGINES = ["chromium", "firefox", "webkit"]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/hop"):
            self._send(302, {"Location": "/final"})
            return
        self._send(200, {"Content-Type": "text/html"}, b"<!doctype html><title>final</title>")

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture
def base():  # type: ignore[no-untyped-def]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


async def _final_request_after_redirect(session: Any, url: str) -> Any:
    seen: list[Any] = []
    session.page.on("response", lambda response: seen.append(response))
    await session.navigate(url)
    finals = [r for r in seen if r.url.endswith("/final")]
    assert finals, [r.url for r in seen]
    return finals[-1].request


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ENGINES)
@pytest.mark.parametrize("guarded", [False, True], ids=["engine-followed", "guard-served"])
async def test_redirected_from_of_a_navigation_redirect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base: str, kind: str, guarded: bool
) -> None:
    pytest.importorskip("playwright")
    _configure_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    pool = BrowserPool()
    try:
        try:
            launched = await pool.launch(kind=kind, headed=False, url=f"{base}/final")
        except Exception as exc:
            _maybe_skip_live_engine(exc)
        session = pool.get(launched["instance_id"])
        if guarded:
            await session.inject_headers(f"{base}/**", {"X-Fixture-Scope": "redirected-from"})

        request = await _final_request_after_redirect(session, f"{base}/hop")

        assert session.page.url == f"{base}/final"
        if guarded:
            assert request.redirected_from is None
        else:
            assert request.redirected_from is not None
            assert request.redirected_from.url == f"{base}/hop"
    finally:
        await pool.shutdown()
