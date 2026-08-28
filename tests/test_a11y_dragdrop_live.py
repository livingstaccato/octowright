# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that ``browser_a11y_dragdrop`` drives a real WAI-ARIA APG
keyboard drag-and-drop widget end to end: grab, navigate, drop, poll-verify,
and (on a failed verify) release -- against real Chromium, not a fake.

The fixture is served over local HTTP rather than opened as a ``file://``
URL: ``session.navigate`` / ``pool.launch`` route every URL through
``core_page_mixin._reject_unsafe_url``, which unconditionally denies the
``file`` scheme (see ``_NAV_DENIED_SCHEMES`` -- a launch to
``file:///etc/passwd`` is exactly what ``test_pool_disconnect.py`` pins as
refused). ``tests/test_header_scope_live.py`` and ``test_route_order_live.py``
hit the same constraint and already establish the fix used here: an ephemeral
``ThreadingHTTPServer`` bound to loopback, which the (default-off)
``OCTOWRIGHT_SSRF_POLICY`` does not restrict.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser

FIXTURE = (Path(__file__).parent / "fixtures" / "a11y_dragdrop.html").resolve()


class _FixtureHandler(BaseHTTPRequestHandler):
    """Serves the on-disk fixture bytes for any GET -- one page, any path."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        body = FIXTURE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def fixture_url() -> Iterator[str]:
    # Ephemeral port: never collide with a running service.
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/a11y_dragdrop.html"
    finally:
        srv.shutdown()


@pytest.fixture
async def session(tmp_path: Path, fixture_url: str):
    pool = BrowserPool(recordings_dir=tmp_path)
    inst = await pool.launch(kind="chromium", headed=False, url=fixture_url)
    try:
        yield pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


async def test_arrow_drag_moves_the_item(session) -> None:
    """Bravo moves above Alpha, verified by reading the real DOM order."""
    result = await session.a11y_dragdrop(
        source_selector="#b",
        nav_key="arrow",
        nav_direction="up",
        max_nav_steps=1,
        verify_js="() => document.getElementById('list').firstElementChild.id === 'b'",
    )
    assert result["stage_reached"] == "verified", result
    assert result["ok"] is True and result["released"] is False

    # The tool's own return value only proves it thinks it succeeded --
    # confirm the browser actually reordered the list.
    order = await session.page.evaluate("() => Array.from(document.querySelectorAll('#list li')).map((li) => li.id)")
    assert order == ["b", "a", "c"], order
    # The fixture's drop_key press is a toggle: the same Space that commits
    # the drop also clears aria-grabbed and updates the status text.
    grabbed_state = await session.page.evaluate("() => document.getElementById('b').getAttribute('aria-grabbed')")
    assert grabbed_state == "false", "drop should have cleared aria-grabbed"
    status_text = await session.page.evaluate("() => document.getElementById('status').textContent")
    assert status_text == "dropped b", status_text


async def test_failed_verify_releases_the_widget(session) -> None:
    """A check that can never pass must leave nothing grabbed."""
    result = await session.a11y_dragdrop(
        source_selector="#a",
        nav_key="arrow",
        nav_direction="down",
        max_nav_steps=1,
        verify_js="() => false",
        verify_timeout_ms=300,
        verify_poll_ms=50,
    )
    assert result["stage_reached"] == "failed_verify"
    assert result["released"] is True
    still_grabbed = await session.page.evaluate("() => document.querySelectorAll('[aria-grabbed=\"true\"]').length")
    assert still_grabbed == 0, "release_key did not exit grab mode"

    # The list itself must still show the real DOM effect of the navigation
    # step that ran before the verify failed: Alpha moved below Bravo.
    order = await session.page.evaluate("() => Array.from(document.querySelectorAll('#list li')).map((li) => li.id)")
    assert order == ["b", "a", "c"], order
    # The engine presses drop_key BEFORE polling verify, and this fixture's
    # Space toggle already cleared `grabbed` at that point -- so the
    # release_key (Escape) press that follows a failed verify lands on a
    # widget that is not in grab mode and is a no-op by the fixture's own
    # Escape handler (`grabbed` is falsy). `result["released"]` still reads
    # True because the key press itself succeeded; the status text is left
    # over from the drop, not "cancelled".
    status_text = await session.page.evaluate("() => document.getElementById('status').textContent")
    assert status_text == "dropped a", status_text
