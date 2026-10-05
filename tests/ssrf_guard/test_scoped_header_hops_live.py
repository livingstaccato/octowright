# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A URL-scoped header is matched per hop of a navigation, with the SSRF policy off.

A route's ``fallback(headers=...)`` override is re-applied by Playwright to
every redirect the request starts, so a header scoped to one origin rode a
``302`` to another. A session with scoped headers now routes its navigations
through the guard's single-fetch path even with the policy off: each hop is
its own navigation, re-matched against the patterns.

Two origins on one server: ``127.0.0.1`` (the scoped one) and ``localhost``.
Every assertion reads what the server actually received.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from octowright.browser_pool import BrowserPool
from tests.test_engine_matrix_live import _configure_runtime_paths, _maybe_skip_live_engine

pytestmark = pytest.mark.live_browser

HEADER = "X-Scoped-Token"
VALUE = "Fixture-Not-A-Real-Secret-scoped-hop"  # pragma: allowlist secret (obviously fake fixture)
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
        parts = urlsplit(self.path)
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.seen[(host, parts.path)] = {k.lower(): v for k, v in self.headers.items()}  # type: ignore[attr-defined]
        if parts.path == "/redir":
            target = parse_qs(parts.query)["to"][0]
            self._send(302, {"Location": target, "Set-Cookie": "hop=from-the-3xx; Path=/"})
            return
        body = b'<!doctype html><title>ok</title><a id="rel" href="rel-target">rel</a>'
        self._send(200, {"Content-Type": "text/html"}, body)

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture
def server():  # type: ignore[no-untyped-def]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.seen = {}  # type: ignore[attr-defined]
    srv.lock = threading.Lock()  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()


def _received(srv: Any, host: str, path: str) -> dict[str, str]:
    """Headers *host* received for *path*; fails if the request never arrived."""
    with srv.lock:
        seen = dict(srv.seen)
    assert (host, path) in seen, f"server never received {host}{path}; saw {sorted(seen)}"
    return seen[(host, path)]


async def _launch(pool: BrowserPool, kind: str, url: str, **kwargs: Any) -> Any:
    try:
        launched = await pool.launch(kind=kind, headed=False, url=url, **kwargs)
    except Exception as exc:
        _maybe_skip_live_engine(exc)
    return pool.get(launched["instance_id"])


async def _assert_per_hop(session: Any, srv: Any, scoped: str, other: str) -> None:
    await session.navigate(f"{scoped}/redir?to={other}/landing")
    assert _received(srv, "127.0.0.1", "/redir").get(HEADER.lower()) == VALUE
    assert HEADER.lower() not in _received(srv, "localhost", "/landing")
    assert session.page.url == f"{other}/landing"
    href = await session.page.evaluate("document.getElementById('rel').href")
    assert href == f"{other}/rel-target"
    cookies = await session.context.cookies(scoped)
    assert any(c["name"] == "hop" and c["value"] == "from-the-3xx" for c in cookies)

    # The other way: a hop that DOES match gets the header.
    await session.navigate(f"{other}/redir?to={scoped}/matched")
    assert HEADER.lower() not in _received(srv, "localhost", "/redir")
    assert _received(srv, "127.0.0.1", "/matched").get(HEADER.lower()) == VALUE
    assert session.page.url == f"{scoped}/matched"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ENGINES)
async def test_launch_scoped_headers_are_matched_per_hop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: Any, kind: str
) -> None:
    pytest.importorskip("playwright")
    _configure_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    port = server.server_address[1]
    scoped, other = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
    pool = BrowserPool()
    try:
        session = await _launch(
            pool,
            kind,
            f"{scoped}/start",
            extra_http_headers={HEADER: VALUE},
            extra_http_headers_urls=[f"{scoped}/**"],
        )
        await _assert_per_hop(session, server, scoped, other)
        await pool.close(session.instance_id, force=True)
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ENGINES)
async def test_injected_headers_are_matched_per_hop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: Any, kind: str
) -> None:
    pytest.importorskip("playwright")
    _configure_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    port = server.server_address[1]
    scoped, other = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
    pool = BrowserPool()
    try:
        session = await _launch(pool, kind, f"{other}/start")
        assert session.context._impl_obj._routes == []
        await session.inject_headers(f"{scoped}/**", {HEADER: VALUE})
        await _assert_per_hop(session, server, scoped, other)
        await pool.close(session.instance_id, force=True)
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ENGINES)
async def test_a_session_without_scoped_headers_registers_no_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: Any, kind: str
) -> None:
    """Default installs pay nothing: unscoped launch headers ride the context, not a route."""
    pytest.importorskip("playwright")
    _configure_runtime_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    port = server.server_address[1]
    pool = BrowserPool()
    try:
        session = await _launch(pool, kind, f"http://127.0.0.1:{port}/start", extra_http_headers={HEADER: VALUE})
        assert session.context._impl_obj._routes == []
        assert session.context._impl_obj._web_socket_routes == []
        await pool.close(session.instance_id, force=True)
    finally:
        await pool.shutdown()
