# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that the credential-fill check reads the page as it is, on every engine.

The unit tests fake ``page.url``. What only a browser shows is that the origin
read before a fill is the document's real one: after a navigation octowright
did not make (``session.url`` still names the launch page) and inside a
cross-origin iframe whose top page is the launch origin. Two loopback ports are
two origins, which is the ``localhost:3000`` vs ``localhost:45678`` case.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.macros import execution

pytestmark = pytest.mark.live_browser

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential
LOGIN = b'<!doctype html><html><body><input type="password" id="pw"></body></html>'


def _server(body: bytes) -> ThreadingHTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def origins() -> Iterator[tuple[str, str]]:
    foreign = _server(LOGIN)
    foreign_url = f"http://127.0.0.1:{foreign.server_address[1]}/"
    home = _server(
        LOGIN[: -len(b"</body></html>")] + f'<iframe id="embed" src="{foreign_url}"></iframe></body></html>'.encode()
    )
    try:
        yield f"http://127.0.0.1:{home.server_address[1]}/", foreign_url
    finally:
        home.shutdown()
        foreign.shutdown()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def session(request: pytest.FixtureRequest, tmp_path: Path, origins: tuple[str, str]) -> Any:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        inst = await pool.launch(kind=request.param, headed=False, url=origins[0])
    except Exception as exc:  # engine not installed on this host
        await pool.shutdown()
        pytest.skip(f"{request.param} unavailable: {exc}")
    try:
        yield pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


FILL = {"action": "fill", "selector": "#pw", "value": "{{password}}"}


async def _run(monkeypatch: pytest.MonkeyPatch, session: Any, actions: list[dict[str, Any]]) -> Any:
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    return await execution.run_macro(session, "m", {"password": SECRET})


async def test_a_fill_on_the_launch_origin_types_the_credential(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run(monkeypatch, session, [FILL])
    assert await session.page.input_value("#pw") == SECRET


async def test_a_page_the_script_navigated_to_is_foreign(
    session: Any, monkeypatch: pytest.MonkeyPatch, origins: tuple[str, str]
) -> None:
    """``session.url`` still names the launch page; the document does not."""
    home, foreign = origins
    await session.page.evaluate(f"location.href = {foreign!r}")
    await session.page.wait_for_url(foreign)
    assert session.url.startswith(home)
    with pytest.raises(RuntimeError, match=foreign.rstrip("/").replace(".", r"\.")):
        await _run(monkeypatch, session, [FILL])
    assert await session.page.input_value("#pw") == ""


async def test_a_cross_origin_iframe_on_the_launch_page_is_foreign(
    session: Any, monkeypatch: pytest.MonkeyPatch, origins: tuple[str, str]
) -> None:
    _home, foreign = origins
    frame_step = {"action": "switch_frame", "selector": "#embed"}
    with pytest.raises(RuntimeError, match=foreign.rstrip("/").replace(".", r"\.")):
        await _run(monkeypatch, session, [frame_step, FILL])
    frame = session.page.frame(url=foreign)
    assert frame is not None and await frame.input_value("#pw") == ""


async def test_the_step_may_list_the_foreign_origin(
    session: Any, monkeypatch: pytest.MonkeyPatch, origins: tuple[str, str]
) -> None:
    _home, foreign = origins
    await _run(
        monkeypatch,
        session,
        [{"action": "navigate", "url": foreign}, {**FILL, "allowed_origins": [foreign.rstrip("/")]}],
    )
    assert await session.page.input_value("#pw") == SECRET
