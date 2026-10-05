# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A replacement sends what the original was sending, from its first request.

Read off the wire against a local server. Before, a handoff's replacement (a
new context) arrived without the original's ``inject_headers`` routes, its
``mock_route`` mocks or its page-level ``set_extra_http_headers``, and a
crash-recovered page (a new page in the same context) without the mocks and
page headers -- silently. The handoff case also pins that the FIRST
navigation carries them: replayed after the launch, the reopen of the current
URL would have gone out bare.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool import crash_recovery, incidents
from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser

_INJECT = ("**/page", {"X-Fixture-Inject": "inject-1"})
_MOCK = "**/api/mocked"
_PAGE = {"X-Fixture-Page": "page-1"}


class _Recording(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        srv: Any = self.server
        srv.seen.setdefault(self.path, []).append({k.lower(): v for k, v in self.headers.items()})
        body = b"<!doctype html><title>page</title>" if self.path == "/page" else b"real"
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if self.path == "/page" else "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[Any]:
    srv: Any = ThreadingHTTPServer(("127.0.0.1", 0), _Recording)
    srv.daemon_threads = True
    srv.seen = {}
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


async def _launch_with_routes(pool: BrowserPool, kind: str, base: str) -> Any:
    try:
        inst = await pool.launch(kind=kind, headed=False, ephemeral=True, url=f"{base}/page")
    except Exception as exc:  # engine not installed on this host
        pytest.skip(f"{kind} unavailable: {exc}")
    session = pool.get(inst["instance_id"])
    await session.inject_headers(*_INJECT)
    await session.mock_route(_MOCK, status=200, body="mocked-body", content_type="text/plain")
    await session.set_extra_http_headers(dict(_PAGE))
    return session


async def _fetch(session: Any, path: str) -> str:
    return str(await session.page.evaluate("p => fetch(p).then(r => r.text())", path))


def _assert_sent(server: Any) -> None:
    last = server.seen["/page"][-1]
    assert last.get("x-fixture-inject") == "inject-1", last
    assert last.get("x-fixture-page") == "page-1", last


async def test_a_handoff_replacement_carries_routes_and_headers(kind: str, server: Any, tmp_path: Path) -> None:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        original = await _launch_with_routes(pool, kind, server.base)
        server.seen.clear()

        result = await pool.handoff(original.instance_id, accept_stateless=True)

        assert "warnings" not in result, result
        fresh = pool.get(result["new_instance_id"])
        # Its FIRST navigation -- the reopen of /page -- already carried both headers.
        assert len(server.seen["/page"]) == 1
        _assert_sent(server)
        assert await _fetch(fresh, "/api/mocked") == "mocked-body"
        assert "/api/mocked" not in server.seen
        state = fresh.header_state()
        assert state["injected"] == {_INJECT[0]: _INJECT[1]} and state["page"] == _PAGE
        # Through the replacement's own methods, so its recording says so too.
        actions = [json.loads(line)["action"] for line in Path(fresh.log_path).read_text().splitlines()]
        assert {"inject_headers", "mock_route", "set_extra_http_headers"} <= set(actions)
        assert actions.index("inject_headers") < actions.index("mock_route") < actions.index("set_extra_http_headers")
    finally:
        await pool.shutdown()


async def test_a_mock_on_a_page_the_replacement_does_not_reopen_is_a_warning(
    kind: str, server: Any, tmp_path: Path
) -> None:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        original = await _launch_with_routes(pool, kind, server.base)
        # The mock's page is no longer the active one (as after page_switch).
        original._mock_specs[_MOCK] = original._mock_specs[_MOCK].__class__(
            **original._mock_specs[_MOCK].kwargs(), page=object()
        )

        result = await pool.handoff(original.instance_id, accept_stateless=True)

        assert any(_MOCK in warning for warning in result.get("warnings", [])), result
        fresh = pool.get(result["new_instance_id"])
        assert await _fetch(fresh, "/api/mocked") == "real"
    finally:
        await pool.shutdown()


async def test_crash_recovery_restores_page_routes_and_headers(kind: str, server: Any, tmp_path: Path) -> None:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        session = await _launch_with_routes(pool, kind, server.base)
        incidents.reset()
        dead = session.page
        server.seen.clear()

        assert await crash_recovery._recover(session, dead, 15_000, f"{server.base}/page") is True

        assert session.page is not dead
        _assert_sent(server)
        assert await _fetch(session, "/api/mocked") == "mocked-body"
        assert "/api/mocked" not in server.seen
        (incident,) = incidents.recent(category=incidents.CATEGORY_RENDERER_CRASH)
        assert "route_warnings" not in incident, incident
        # The restored mock is the session's to remove, on the page it now lives on.
        assert session._mock_specs[_MOCK].page is session.page
        await session.unmock_route(_MOCK)
        assert await _fetch(session, "/api/mocked") == "real"
    finally:
        await pool.shutdown()
