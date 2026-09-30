# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that page script cannot drive the viewport pill's binding.

The pill talks to Python through ``__octowright_viewport_action``, which
``expose_binding`` installs on ``window`` -- the same global object hostile
page script runs against. Each attack below was measured to work against the
pill as it was:

* wrapping the global and dispatching a synthetic ``resize``: the pill's
  debounced refresh re-read the global and handed the wrapper its token;
* hooking ``JSON.stringify``: Playwright's binding controller serialises every
  call's payload with the *page's* ``JSON.stringify`` at call time, so even a
  pill holding a private copy of the binding leaked a token it sent;
* calling ``.click()`` on the pill's own buttons: the pill lives in the page's
  DOM, and an untrusted click ran ``relaunch-fluid`` with no token needed.

Real engines because the defence depends on two things only a browser can
confirm: that the binding already exists when the pill's init script runs, and
that locking the global holds against later page assignment.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser

#: Every observation the page makes lands on ``window.__attack``.
ATTACK_PAGE = b"""<!doctype html><html><body><p>hostile</p><script>
window.__attack = { stolen: [], replaced: null, modal_opened_untrusted: null };
const note = (value) => {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text && text.indexOf("token") !== -1) window.__attack.stolen.push(text);
};
// 1. Wrap the binding global.
try {
  const original = window.__octowright_viewport_action;
  window.__octowright_viewport_action = (payload) => { note(payload); return original(payload); };
  window.__attack.replaced = window.__octowright_viewport_action !== original;
} catch (err) { window.__attack.replaced = "threw: " + err; }
// 2. Hook the serialiser Playwright's controller uses at call time.
const realStringify = JSON.stringify;
JSON.stringify = function (value, ...rest) {
  const out = realStringify.call(JSON, value, ...rest);
  if (typeof out === "string" && out.indexOf("token") !== -1) window.__attack.stolen.push(out);
  return out;
};
// Poke the pill: its resize handler schedules a refresh through the binding.
window.dispatchEvent(new Event("resize"));
// 3. Drive the pill's own UI with untrusted events.
setTimeout(() => {
  window.dispatchEvent(new KeyboardEvent("keydown", { altKey: true, bubbles: true }));
  const root = document.getElementById("__octowright_viewport_status__");
  if (root) root.click();
  window.__attack.modal_opened_untrusted = !!document.getElementById("__octowright_viewport_modal__");
}, 400);
// ...and once a human has opened the modal, click its buttons for them.
window.__clickButtons = () => {
  const modal = document.getElementById("__octowright_viewport_modal__");
  const buttons = modal ? [...modal.querySelectorAll("button")] : [];
  for (const button of buttons) button.click();
  return buttons.length;
};
</script></body></html>"""


PLAIN_PAGE = b"<!doctype html><html><body><p>plain</p></body></html>"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = PLAIN_PAGE if self.path.startswith("/plain") else ATTACK_PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
async def launched(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        inst = await pool.launch(kind=request.param, headed=False, url="about:blank")
    except Exception as exc:  # engine not installed on this host
        await pool.shutdown()
        pytest.skip(f"{request.param} unavailable: {exc}")
    try:
        yield pool, pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


async def test_page_script_cannot_obtain_the_token_or_drive_the_binding(
    launched: Any, page_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, session = launched
    calls: list[tuple[str, ...]] = []

    async def _relaunch(instance_id: str) -> dict[str, Any]:
        calls.append(("relaunch-fluid", instance_id))
        return {"new_instance_id": "never"}

    async def _sync() -> dict[str, Any]:
        calls.append(("sync",))
        return {"width": 1, "height": 1}

    monkeypatch.setattr(pool, "relaunch_fluid", _relaunch)
    monkeypatch.setattr(session, "viewport_sync", _sync)

    page = session.page
    await page.goto(page_url)
    await asyncio.sleep(1.2)  # past the 150ms refresh debounce and the 400ms UI poke
    # A human opens the modal (trusted input); the page then clicks its buttons.
    await page.keyboard.down("Alt")
    try:
        await asyncio.sleep(1.2)  # ALT_HOLD_MS before the pill takes clicks
        await page.click("#__octowright_viewport_status__")
        clicked = await page.evaluate("() => window.__clickButtons()")
        await asyncio.sleep(0.5)
    finally:
        await page.keyboard.up("Alt")
    attack = await page.evaluate("() => window.__attack")

    assert attack["replaced"] is not True, "page script replaced the binding global"
    assert attack["modal_opened_untrusted"] is False
    assert attack["stolen"] == [], attack["stolen"]
    assert session.viewport_action_token not in str(attack)
    assert calls == [], calls
    # The page really reached both buttons; otherwise the untrusted-click
    # half of this test proved nothing.
    assert clicked == 2


async def test_a_real_click_on_the_pill_still_syncs(
    launched: Any, page_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No regression: the trusted path -- Alt held, real clicks -- still reaches Python.

    Playwright's input events are trusted, exactly like a human's, so this is
    the path the pill exists for and the one the untrusted-click guard must
    leave alone.
    """
    _pool, session = launched
    calls: list[str] = []

    async def _sync() -> dict[str, Any]:
        calls.append("sync")
        return {"width": 800, "height": 600}

    monkeypatch.setattr(session, "viewport_sync", _sync)
    page = session.page
    await page.goto(page_url + "plain")
    await page.keyboard.down("Alt")
    try:
        await asyncio.sleep(1.2)  # ALT_HOLD_MS before the pill takes clicks
        await page.click("#__octowright_viewport_status__")
        await page.click("#__octowright_viewport_modal__ button >> nth=0")
        for _ in range(50):
            if calls:
                break
            await asyncio.sleep(0.05)
    finally:
        await page.keyboard.up("Alt")
    assert calls == ["sync"]
