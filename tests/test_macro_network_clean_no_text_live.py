# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof for ``expect_network_clean`` / ``expect_no_text`` in a macro run.

Real engines, because the unit tests assume two things only a browser can
confirm: that ``pageerror`` reaches the session, and what text each engine
puts in ``request.failure`` for a refused connection versus an abort. Served
over loopback HTTP because ``file://`` is refused by navigation (see
``test_a11y_dragdrop_live.py``).
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.macros import execution

pytestmark = pytest.mark.live_browser

PAGE = b"""<!doctype html><html><body><h1>Welcome back</h1>
<input type="password" id="pw" value="hunter2-Correct-Horse!"></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/slow"):
            time.sleep(3)  # still in flight when the page navigates away
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args: object) -> None:
        return


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def page_url() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def session(request: pytest.FixtureRequest, tmp_path: Path, page_url: str) -> Any:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        inst = await pool.launch(kind=request.param, headed=False, url=page_url)
    except Exception as exc:  # engine not installed on this host
        await pool.shutdown()
        pytest.skip(f"{request.param} unavailable: {exc}")
    try:
        yield pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


def _macros(monkeypatch: pytest.MonkeyPatch, macros: dict[str, list[dict[str, Any]]]) -> None:
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})


async def _settle(session: Any) -> None:
    # Let the page's fire-and-forget fetch fail and its event reach the session.
    await session.page.wait_for_timeout(500)


async def test_failures_before_the_run_do_not_count(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _closed_port()
    await session.page.evaluate(
        f"() => {{ fetch('http://127.0.0.1:{port}/').catch(() => {{}}); setTimeout(() => {{ throw new Error('x'); }}); }}"
    )
    await _settle(session)
    assert session.network_failures_since_mark() == (1, 1), list(session._network_requests)

    _macros(monkeypatch, {"clean": [{"action": "expect_network_clean"}]})
    result = await execution.run_macro(session, "clean")
    assert result["executed"] == 1


async def test_failures_during_the_run_fail_it(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    port = _closed_port()
    _macros(
        monkeypatch,
        {
            "dirty": [
                {
                    "action": "evaluate",
                    "expression": f"() => {{ fetch('http://127.0.0.1:{port}/').catch(() => {{}}); setTimeout(() => {{ throw new Error('x'); }}); }}",
                },
                {"action": "wait_for", "selector": "h1"},
                {"action": "evaluate", "expression": "() => new Promise((r) => setTimeout(r, 500))"},
                {"action": "expect_network_clean"},
            ]
        },
    )
    with pytest.raises(RuntimeError, match=r"1 failed request\(s\), 1 page error\(s\)"):
        await execution.run_macro(session, "dirty")


async def test_navigating_away_mid_request_is_not_a_failure(session: Any, page_url: str) -> None:
    """Whatever the engine calls an abort, it must be in ABORTED_REQUEST_FAILURES."""
    session.mark_network_clean_window()
    await session.page.evaluate("() => { fetch('/slow-never-answers-' + Math.random()); }")
    await session.page.goto(page_url + "?next")
    await _settle(session)
    failures = [row["failure"] for row in session._network_requests if row.get("failure")]
    assert session.network_failures_since_mark()[0] == 0, failures


async def test_no_text_on_a_rendered_page(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The password sits in an input's value: rendered text does not include it."""
    _macros(
        monkeypatch,
        {
            "absent": [{"action": "expect_no_text", "text": "{{password}}"}],
            "present": [{"action": "expect_no_text", "text": "Welcome"}],
        },
    )
    await execution.run_macro(session, "absent", {"password": "hunter2-Correct-Horse!"})
    with pytest.raises(RuntimeError) as excinfo:
        await execution.run_macro(session, "present")
    assert "forbidden text" in str(excinfo.value)
    assert "Welcome" not in str(excinfo.value)
