# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A password typed directly stays out of every durable row the page echoes it into.

``OCTOWRIGHT_REDACT_INPUTS`` has always hidden a password field's value in the
``fill`` row. The page is free to repeat it, though, and a login form that
``console.log``s its own field (debug builds do) or renders it back put the
cleartext into the recording's console rows and the markdown cache. Real
engines, because the echo is the page's own console event and rendered text.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.defaults import REDACTED_INPUT_PLACEHOLDER
from octowright.macros import execution
from octowright.macros.privacy import REDACTED

pytestmark = pytest.mark.live_browser

SECRET = "Hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential
PAGE = b"""<!doctype html><html><body><h1>Sign in</h1>
<input type="password" id="pw"><p id="echo"></p>
<script>
document.getElementById("pw").addEventListener("input", (e) => {
  console.log("typed " + e.target.value);
  document.getElementById("echo").textContent = "You typed " + e.target.value;
});
window.echoToServer = (value) => fetch("/echo", {method: "POST", body: value});
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self) -> None:
        # A same-origin failure that echoes what it was sent, as a login API
        # reporting the rejected value would.
        sent = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = b'{"error": "rejected ' + sent + b'"}'
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


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


def _rows(session: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(session.log_path).read_text(encoding="utf-8").splitlines() if line]


async def _console_rows(session: Any, count: int) -> list[dict[str, Any]]:
    for _ in range(100):
        rows = [row for row in _rows(session) if row.get("action") == "console" and "typed" in str(row.get("text"))]
        if len(rows) >= count:
            return rows
        await asyncio.sleep(0.05)
    raise AssertionError(f"expected {count} console row(s), got {_rows(session)}")


async def test_a_page_echo_of_a_filled_password_is_scrubbed(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_REDACT_INPUTS", raising=False)
    await session.fill("#pw", SECRET)
    console = await _console_rows(session, 1)
    assert console[-1]["text"] == f"typed {REDACTED}"
    fill = next(row for row in _rows(session) if row.get("action") == "fill")
    assert fill["value"] == REDACTED_INPUT_PLACEHOLDER

    path = await session.capture_markdown(force=True)
    assert path is not None
    markdown = path.read_text(encoding="utf-8")
    assert "You typed" in markdown and SECRET not in markdown

    # A later macro run appends to the ledger; it must not replace it.
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": []})
    await execution.run_macro(session, "noop", {"password": "another-value-9x"})  # pragma: allowlist secret
    await session.page.evaluate("() => document.getElementById('pw').dispatchEvent(new Event('input'))")
    await _console_rows(session, 2)
    assert SECRET not in Path(session.log_path).read_text(encoding="utf-8")


async def _body_landed(session: Any) -> dict[str, Any]:
    for _ in range(100):
        rows = [row for row in session.get_network_requests(limit=None)["requests"] if row["url"].endswith("/echo")]
        if rows and "body" in rows[-1]:
            return rows[-1]
        await asyncio.sleep(0.05)
    raise AssertionError(f"no /echo body: {session.get_network_requests(limit=None)}")


async def test_the_live_console_and_network_buffers_are_scrubbed_too(
    session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What browser_console_messages, browser_network_requests, their summaries
    and the dashboard's live /console read -- not only the JSONL."""
    monkeypatch.delenv("OCTOWRIGHT_REDACT_INPUTS", raising=False)
    await session.fill("#pw", SECRET)
    await _console_rows(session, 1)
    await session.page.evaluate("(v) => window.echoToServer(v)", SECRET)
    row = await _body_landed(session)

    assert [entry["text"] for entry in session.console if "typed" in entry["text"]] == [f"typed {REDACTED}"]
    assert row["body"] == f'{{"error": "rejected {REDACTED}"}}'
    assert SECRET not in json.dumps(list(session.console))
    assert SECRET not in json.dumps(session.get_network_requests(limit=None, include_headers=True))


async def test_a_screenshot_after_a_password_fill_takes_the_privacy_boundary(
    session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page renders the typed value ("You typed ..."). Chromium redacts and
    proves it before capturing; the other engines cannot prove it, so they
    refuse and leave no file -- never a raw PNG holding the password."""
    monkeypatch.delenv("OCTOWRIGHT_REDACT_INPUTS", raising=False)
    await session.fill("#pw", SECRET)
    target = Path(session.log_path).with_suffix(".png")

    if session.kind == "chromium":
        await session.screenshot(target)
        assert target.exists() and target.stat().st_size > 0
        echoed = await session.page.evaluate("() => document.getElementById('echo').textContent")
        assert echoed == f"You typed {SECRET}", "the page must be restored after the capture"
    else:
        with pytest.raises(RuntimeError, match="refused"):
            await session.screenshot(target)
        assert not target.exists()
