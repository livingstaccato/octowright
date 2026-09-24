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

import asyncio
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

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential
PAGE = b"""<!doctype html><html><body><h1>Welcome back</h1>
<input type="password" id="pw" value="hunter2-Correct-Horse!"></body></html>"""


#: One page per surface a forbidden text can be rendered on; each token appears once.
SURFACES = b"""<!doctype html><html><head><style>#gen::after { content: "TOKEN-GENERATED"; }</style></head><body>
<div class="message">hello</div><div class="message">TOKEN-SECOND-MATCH</div>
<div id="open-host"></div><div id="closed-host"></div><div id="gen"></div>
<input type="text" value="TOKEN-TEXT-INPUT"><input placeholder="TOKEN-PLACEHOLDER">
<input type="password" value="TOKEN-PASSWORD-INPUT">
<p>TOKEN-ZERO&#8203;WIDTH</p>
<div style="display:none">TOKEN-HIDDEN</div>
<iframe srcdoc="<p>TOKEN-IFRAME</p>"></iframe>
<script>
document.getElementById("open-host").attachShadow({mode: "open"}).innerHTML = "<span>TOKEN-OPEN-SHADOW</span>";
document.getElementById("closed-host").attachShadow({mode: "closed"}).innerHTML = "<span>TOKEN-CLOSED-SHADOW</span>";
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/slow"):
            time.sleep(3)  # still in flight when the page navigates away
        if self.path.startswith("/hang"):
            time.sleep(20)  # never answers within a test
        status = {"/api500": 500, "/missing.png": 404}.get(self.path, 200)
        body = SURFACES if self.path.startswith("/surfaces") else PAGE
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


_REFUSED_AND_THROWN = (
    "() => {{ fetch('http://127.0.0.1:{port}/').catch(() => {{}}); setTimeout(() => {{ throw new Error('x'); }}); }}"
)


async def test_failures_before_the_run_do_not_count(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session.enable_inflight_tracking()
    await session.page.evaluate(_REFUSED_AND_THROWN.format(port=_closed_port()))
    await session._settle_network(5000)
    assert session.network_failures_since()[:2] == (1, 1), list(session._network_requests)

    _macros(monkeypatch, {"clean": [{"action": "expect_network_clean"}]})
    result = await execution.run_macro(session, "clean")
    assert result["executed"] == 1


async def test_failures_during_the_run_fail_it(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """No sleep between the step and the check: the settle wait is what must catch the failure."""
    _macros(
        monkeypatch,
        {
            "dirty": [
                {"action": "evaluate", "expression": _REFUSED_AND_THROWN.format(port=_closed_port())},
                {"action": "expect_network_clean"},
            ]
        },
    )
    with pytest.raises(RuntimeError, match=r"1 failed request\(s\), 1 page error\(s\)"):
        await execution.run_macro(session, "dirty")


async def test_a_cancelled_request_is_observed_and_classified_as_an_abort(session: Any) -> None:
    """Not tautological: the engine must report a cancellation, and it must be one on the list.

    AbortController is the ordinary way an app cancels a fetch, and it is the one
    cancellation all three engines report (measured: net::ERR_ABORTED,
    NS_BINDING_ABORTED, "Load request cancelled").
    """
    session.enable_inflight_tracking()
    session.mark_network_clean_window()
    await session.page.evaluate(
        "() => { const c = new AbortController();"
        " fetch('/slow-cancelled', {signal: c.signal}).catch(() => {}); setTimeout(() => c.abort(), 150); }"
    )
    await session._settle_network(5000)
    failures = [row["failure"] for row in session._network_requests if row.get("failure")]
    assert failures, "the engine reported no cancellation, so the abort list was not exercised"
    assert session.network_failures_since()[0] == 0, failures


async def test_navigating_away_mid_request_is_not_a_failure(session: Any, page_url: str) -> None:
    session.enable_inflight_tracking()
    session.mark_network_clean_window()
    await session.page.evaluate("() => { fetch('/slow-never-answers-' + Math.random()).catch(() => {}); }")
    await session.page.goto(page_url + "?next")
    await session._settle_network(5000)
    failures = [row["failure"] for row in session._network_requests if row.get("failure")]
    assert session.network_failures_since()[0] == 0, failures


async def _start_hanging_fetch(session: Any) -> None:
    await session.page.evaluate("() => { fetch('/hang-' + Math.random()).catch(() => {}); }")
    deadline = time.monotonic() + 5
    while session.pending_requests() == 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert session.pending_requests() == 1, "the fetch never registered as in flight"


async def test_a_fetch_cancelled_by_navigation_does_not_hold_the_settle_wait(session: Any, page_url: str) -> None:
    """Measured on Chromium: the cancelled fetch fires neither requestfinished nor requestfailed.

    Without the commit forgetting it, every later settle wait ran to its full
    timeout and reported in_flight >= 1; Firefox and WebKit end it themselves.
    """
    session.enable_inflight_tracking()
    await _start_hanging_fetch(session)
    await session.page.goto(page_url + "?next")
    started = time.monotonic()
    result = await session.expect_network_clean(settle_timeout_ms=3000)
    assert time.monotonic() - started < 2.0, result
    assert result["in_flight"] == 0 and result["failed_requests"] == 0


async def test_a_same_document_navigation_keeps_waiting_for_the_documents_fetch(session: Any) -> None:
    """pushState fires framenavigated too, but the document -- and its fetch -- live on."""
    session.enable_inflight_tracking()
    await _start_hanging_fetch(session)
    await session.page.evaluate("() => history.pushState({}, '', '/spa-route')")
    result = await session.expect_network_clean(settle_timeout_ms=500)
    assert result["in_flight"] == 1


async def test_a_macro_that_asserts_tracks_from_its_first_step(
    session: Any, page_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run enables tracking before dispatch, so the check waits for the step's request."""
    assert not session._inflight_tracking
    _macros(
        monkeypatch,
        {
            "slow": [
                {"action": "evaluate", "expression": "() => { fetch('/slow-then-refused').catch(() => {}); }"},
                {"action": "expect_network_clean", "settle_timeout_ms": 10000},
            ]
        },
    )
    started = time.monotonic()
    await execution.run_macro(session, "slow")
    # /slow answers after 3s: the check returning sooner would mean it never waited.
    assert time.monotonic() - started >= 2.5
    assert session._inflight_tracking


async def test_no_text_on_a_rendered_page(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The password sits in an input's value: rendered text does not include it."""
    _macros(
        monkeypatch,
        {
            "absent": [{"action": "expect_no_text", "text": "{{password}}"}],
            "present": [{"action": "expect_no_text", "text": "Welcome"}],
        },
    )
    await execution.run_macro(session, "absent", {"password": SECRET})
    with pytest.raises(RuntimeError) as excinfo:
        await execution.run_macro(session, "present")
    assert "forbidden text" in str(excinfo.value)
    assert "Welcome" not in str(excinfo.value)


async def test_http_errors_count_api_failures_not_missing_images(session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 500 from an API fails the opt-in check; a 404 image never does."""
    load = [
        {
            "action": "evaluate",
            "expression": "() => { const i = new Image(); i.src = '/missing.png'; document.body.append(i); }",
        },
    ]
    api = [
        {
            "action": "evaluate",
            "expression": "() => { fetch('/api500'); }",
        }
    ]
    _macros(
        monkeypatch,
        {
            "image-only": [*load, {"action": "expect_network_clean", "http_errors": True}],
            "api-default": [*api, {"action": "expect_network_clean"}],
            "api-strict": [*api, {"action": "expect_network_clean", "http_errors": True}],
        },
    )
    await execution.run_macro(session, "image-only")
    await execution.run_macro(session, "api-default")
    with pytest.raises(RuntimeError, match=r"1 HTTP error\(s\)"):
        await execution.run_macro(session, "api-strict")


@pytest.mark.parametrize(
    ("token", "selector"),
    [
        ("TOKEN-SECOND-MATCH", ".message"),  # every match, not just the first
        ("TOKEN-OPEN-SHADOW", "body"),
        ("TOKEN-TEXT-INPUT", "body"),  # a drawn form value
        ("TOKEN-PLACEHOLDER", "body"),
        ("TOKEN-ZEROWIDTH", "body"),  # split by a zero-width character
        ("TOKEN-IFRAME", "body"),  # the page means every frame
        ("TOKEN-GENERATED", "body"),  # CSS generated content
    ],
)
async def test_every_rendered_surface_is_checked(session: Any, page_url: str, token: str, selector: str) -> None:
    """The script scan must catch each one on every engine, Chromium included.

    On Chromium the DOM snapshot runs second and would catch most of these too,
    so a broken collector hid behind it; the message names the scan that failed.
    """
    await session.page.goto(page_url + "surfaces")
    with pytest.raises(RuntimeError, match=r"forbidden text .*\(script scan\)"):
        await session.expect_no_text(token, selector=selector)


@pytest.mark.parametrize("token", ["TOKEN-HIDDEN", "TOKEN-PASSWORD-INPUT"])
async def test_what_is_not_drawn_passes(session: Any, page_url: str, token: str) -> None:
    await session.page.goto(page_url + "surfaces")
    await session.expect_no_text(token)


async def test_a_missing_selector_passes_immediately(session: Any, page_url: str) -> None:
    """Nothing matched, so nothing is rendered; the opposite of a timeout."""
    await session.page.goto(page_url + "surfaces")
    started = time.monotonic()
    await session.expect_no_text("TOKEN-SECOND-MATCH", selector="#error-banner")
    assert time.monotonic() - started < 3


async def test_a_closed_shadow_root_is_checked_on_chromium(session: Any, page_url: str) -> None:
    """Script cannot reach a closed root; Chromium's DOM snapshot can, other engines cannot."""
    await session.page.goto(page_url + "surfaces")
    if session.kind != "chromium":
        pytest.skip("closed shadow roots are only reachable through Chromium's DOM snapshot")
    with pytest.raises(RuntimeError, match=r"forbidden text .*\(DOM snapshot\)"):
        await session.expect_no_text("TOKEN-CLOSED-SHADOW")


async def test_octowrights_own_overlay_text_is_not_the_page(session: Any, page_url: str) -> None:
    """The corner badge shows the session label; a check for that text must not fail on it."""
    await session.page.goto(page_url + "surfaces")
    badge = await session.page.evaluate(
        "() => { const b = document.getElementById('__octowright_badge__'); return b ? b.innerText : ''; }"
    )
    if not badge.strip():
        pytest.skip("no badge text on this page to collide with")
    word = max(badge.split(), key=len)
    await session.expect_no_text(word)
