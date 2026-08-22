# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that ``browser_list``'s header report matches the wire.

``header_state()`` reports what octowright recorded when the header was SET.
That is one step removed from what the browser actually sends, and the gap is
not hypothetical for scoped launch headers: passing
``extra_http_headers_urls`` moves the headers off the context entirely
(``extra_http_headers_kwargs`` returns ``{}``) and onto per-glob context
routes. The report still says ``launch: {...}``, which a reader could
reasonably take as "rides every request" -- and it does not.

So this reads the headers off a real local server and asserts three things
together: the matching URL receives the header, a NON-matching URL does not,
and the report describes exactly that. Unscoped launch headers are included as
the contrast case, because "scoped headers skipped this request" is only
meaningful next to "unscoped headers would not have".

Live rather than unit because the property belongs to Playwright's routing,
not to octowright's kwarg assembly, which the unit tests already cover.
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

_SEEN: dict[str, dict[str, str]] = {}
_LOCK = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        with _LOCK:
            _SEEN[self.path] = {k.lower(): v for k, v in self.headers.items()}
        body = b"<html>ok</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


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


def _headers_at(path: str) -> dict[str, str]:
    """Headers the server actually received for *path*.

    Fails loudly when the request never arrived. Returning an empty map instead
    would make "the glob correctly excluded this header" indistinguishable from
    "the navigation failed and nothing was sent" -- the negative assertion
    below would pass for the wrong reason.
    """
    with _LOCK:
        seen = dict(_SEEN)
    assert path in seen, f"server never received {path!r}; saw {sorted(seen)}"
    return seen[path]


def _header_at(path: str, name: str) -> str | None:
    return _headers_at(path).get(name.lower())


@pytest.mark.asyncio
async def test_unscoped_launch_headers_ride_every_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: str
) -> None:
    """The contrast case. Without it, the scoped assertion below cannot
    distinguish "the glob excluded this request" from "the header never
    worked at all"."""
    pytest.importorskip("playwright")
    _configure_runtime_paths(monkeypatch, tmp_path)

    pool = BrowserPool()
    try:
        try:
            launched = await pool.launch(
                kind="chromium",
                headed=False,
                url=f"{server}/anywhere",
                extra_http_headers={"X-Run-Id": "run-42"},
            )
        except Exception as exc:
            _maybe_skip_live_engine(exc)

        session = pool.get(launched["instance_id"])
        await session.navigate(f"{server}/elsewhere")

        assert _header_at("/anywhere", "X-Run-Id") == "run-42"
        assert _header_at("/elsewhere", "X-Run-Id") == "run-42"

        state = session.header_state()
        assert state["launch"] == {"X-Run-Id": "run-42"}
        # No glob key: the report must not imply a narrowing that isn't there.
        assert "launch_url_patterns" not in state

        await pool.close(launched["instance_id"], force=True)
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_scoped_launch_headers_skip_non_matching_urls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: str
) -> None:
    """The case the report could overstate: `launch` is present, but the
    headers only ride URLs matching `launch_url_patterns`."""
    pytest.importorskip("playwright")
    _configure_runtime_paths(monkeypatch, tmp_path)

    pool = BrowserPool()
    try:
        try:
            launched = await pool.launch(
                kind="chromium",
                headed=False,
                url=f"{server}/api/first",
                extra_http_headers={"X-Run-Id": "run-42"},
                extra_http_headers_urls=["**/api/**"],
            )
        except Exception as exc:
            _maybe_skip_live_engine(exc)

        session = pool.get(launched["instance_id"])
        await session.navigate(f"{server}/public/page")

        # The wire, both ways.
        assert _header_at("/api/first", "X-Run-Id") == "run-42"
        assert _header_at("/public/page", "X-Run-Id") is None

        # And the report says exactly that -- headers plus the globs that bound
        # them, never the headers alone.
        state = session.header_state()
        assert state["launch"] == {"X-Run-Id": "run-42"}
        assert state["launch_url_patterns"] == ["**/api/**"]

        await pool.close(launched["instance_id"], force=True)
    finally:
        await pool.shutdown()


@pytest.mark.asyncio
async def test_a_credential_launch_header_reaches_the_page_but_not_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, server: str
) -> None:
    """Redaction must not be achieved by not sending the header.

    The report scrubs `Authorization`; the browser must still send the real
    value, exactly as the recorder's own redaction works.
    """
    pytest.importorskip("playwright")
    _configure_runtime_paths(monkeypatch, tmp_path)

    pool = BrowserPool()
    try:
        try:
            launched = await pool.launch(
                kind="chromium",
                headed=False,
                url=f"{server}/guarded",
                extra_http_headers={"Authorization": "Bearer real-token"},
            )
        except Exception as exc:
            _maybe_skip_live_engine(exc)

        session = pool.get(launched["instance_id"])

        assert _header_at("/guarded", "Authorization") == "Bearer real-token"
        assert session.header_state()["launch"] == {"Authorization": "<redacted:header>"}

        await pool.close(launched["instance_id"], force=True)
    finally:
        await pool.shutdown()
