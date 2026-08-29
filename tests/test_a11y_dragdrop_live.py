# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that ``browser_a11y_dragdrop`` drives a real WAI-ARIA APG
keyboard drag-and-drop widget end to end: grab, navigate, drop, poll-verify,
and (on a failed verify) release -- against real Chromium, not a fake.

The fixture carries BOTH APG navigation variants, and every ``nav_key`` mode
is driven against one of them. That matters most for ``tab``, which is the
tool's DEFAULT: covering only the arrow-key sortable list left the mode almost
every caller gets by default with no live coverage at all, and the two
variants are not interchangeable -- an arrow widget moves the grabbed element
through the DOM while a Tab widget keeps focus pinned on it and moves a
separate drop-target pointer, which is the case the default grabbed-predicate
(``document.activeElement === el``) is written for.

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
    """A check that can never pass must leave nothing grabbed.

    ``drop_key="Enter"`` is load-bearing, not incidental. With the default
    ``Space`` the fixture's own toggle drops the item and clears
    ``aria-grabbed`` BEFORE the failed verify presses Escape, so the release
    lands on a widget that is not grabbed and this test proved only that a
    key press succeeded. The fixture ignores Enter entirely, so the widget is
    still genuinely grabbed when Escape arrives -- which is the condition the
    release path exists for, and the only way to observe it working.
    """
    result = await session.a11y_dragdrop(
        source_selector="#a",
        nav_key="arrow",
        nav_direction="down",
        max_nav_steps=1,
        drop_key="Enter",
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
    # "cancelled" is written ONLY by the fixture's Escape branch, and that
    # branch only runs while something is grabbed -- so this assertion is what
    # distinguishes a release that recovered a stuck widget from a no-op.
    status_text = await session.page.evaluate("() => document.getElementById('status').textContent")
    assert status_text == "cancelled", status_text


async def _bin_status(session) -> str:
    text = await session.page.evaluate("() => document.getElementById('bin-status').textContent")
    return str(text)


async def test_tab_navigation_drops_into_the_cycled_bin(session) -> None:
    """The DEFAULT nav mode, driven with no ``nav_key`` argument at all.

    Omitting the argument is the point: it pins that the default really is
    ``tab``, which nothing else in this file exercised.
    """
    result = await session.a11y_dragdrop(
        source_selector="#t-item",
        max_nav_steps=1,
        verify_text_contains="dropped into bin-2",
    )
    assert result["stage_reached"] == "verified", result
    assert result["ok"] is True and result["released"] is False
    assert result["nav_steps_taken"] == 1
    assert await _bin_status(session) == "dropped into bin-2"
    # Focus stayed pinned on the grabbed item throughout -- the property the
    # default grabbed-predicate depends on for a Tab-navigated widget.
    focused = await session.page.evaluate("() => document.activeElement.id")
    assert focused == "t-item", focused


async def test_tab_backward_navigation_wraps_to_the_last_bin(session) -> None:
    """``nav_direction='backward'`` resolves to Shift+Tab, not Tab."""
    result = await session.a11y_dragdrop(
        source_selector="#t-item",
        nav_key="tab",
        nav_direction="backward",
        max_nav_steps=1,
        verify_js="() => document.getElementById('bin-status').textContent === 'dropped into bin-3'",
    )
    assert result["stage_reached"] == "verified", result
    assert await _bin_status(session) == "dropped into bin-3"


async def test_keys_mode_sends_the_sequence_in_order(session) -> None:
    """``keys`` mode against a real widget, with an ORDER-SENSITIVE sequence.

    ``End`` jumps to the last bin and ``Shift+Tab`` steps back one, so this
    sequence lands on bin-2 while the reverse order would land on bin-3. A
    sequence whose result is order-independent would pass even if the keys
    were sent backwards or deduplicated.
    """
    result = await session.a11y_dragdrop(
        source_selector="#t-item",
        nav_key="keys",
        nav_key_sequence=["End", "Shift+Tab"],
        verify_text_contains="dropped into bin-2",
    )
    assert result["stage_reached"] == "verified", result
    assert result["nav_steps_taken"] == 2
    assert await _bin_status(session) == "dropped into bin-2"


async def test_tab_widget_failed_verify_releases_the_grab(session) -> None:
    """The release path on the Tab variant, with the widget still grabbed.

    Same ``drop_key='Enter'`` trick as the arrow case: the bins widget only
    commits (and ungrabs) on Space, so Enter leaves it grabbed and the Escape
    that follows the failed verify has something real to recover.
    """
    result = await session.a11y_dragdrop(
        source_selector="#t-item",
        max_nav_steps=2,
        drop_key="Enter",
        verify_js="() => false",
        verify_timeout_ms=300,
        verify_poll_ms=50,
    )
    assert result["stage_reached"] == "failed_verify"
    assert result["released"] is True
    assert await _bin_status(session) == "cancelled"
    grabbed_state = await session.page.evaluate("() => document.getElementById('t-item').getAttribute('aria-grabbed')")
    assert grabbed_state == "false", "release_key did not exit grab mode"
