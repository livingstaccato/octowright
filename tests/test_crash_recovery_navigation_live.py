# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Crash recovery onto a last URL that no longer loads still recovers, and says so.

Only a policy refusal was recovered past; a failed navigation of the last URL
-- here a connection refused -- raised after the new page was wired, leaving
it orphaned in the context and the dead page as ``session.page``. With the
policy off that was every failure, since nothing else raised the refusal type.
The ``recovered`` event also went out before the failure was known and never
said the page was elsewhere.

The dead page here is a live one handed to ``_recover`` directly: what is
under test is the swap and its report, not the crash signal
(``test_recovery_giveup_live.py`` drives a real renderer crash).

Two more ways the report was wrong. A load that timed out AFTER the navigation
committed was reported as "NOT at its last URL" while the page was at it. And a
replacement that itself crashed loading its last URL was swapped in as a
usable recovery elsewhere, while its own crash listener scheduled a second
recovery behind this one. It is now replaced again within the crash-loop
bound, and the recovery ends ``exhausted`` once that is spent.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from playwright.async_api import Page

from octowright import defaults
from octowright.browser_pool import crash_recovery, incidents
from octowright.browser_pool import session_event_bus as _bus
from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(params=["chromium", "firefox", "webkit"])
def kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.mark.parametrize("policy", ["off", "block-private"])
async def test_recovery_onto_a_url_that_fails_to_load_recovers_elsewhere(
    kind: str, policy: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", policy)
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "127.0.0.1")
    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        try:
            inst = await pool.launch(kind=kind, headed=False, url="data:text/html,<title>start</title>")
        except Exception as exc:  # engine not installed on this host
            pytest.skip(f"{kind} unavailable: {exc}")
        session = pool.get(inst["instance_id"])
        incidents.reset()
        dead = session.page
        unreachable = f"http://127.0.0.1:{_closed_port()}/gone"
        assert await crash_recovery._recover(session, dead, 15_000, unreachable) is True
        assert session.page is not dead and not session.page.is_closed()
        assert dead.is_closed() and dead not in session.pages and session.page in session.pages
        assert session._crashed is False
        (incident,) = incidents.recent(category=incidents.CATEGORY_RENDERER_CRASH)
        assert incident["outcome"] == "recovered" and incident["navigation_error"]
        (event,) = [e for e in events if getattr(e, "outcome", None) == "recovered"]
        assert event.recovered_elsewhere is True and event.navigation_error == incident["navigation_error"]
        # The session is usable: the next navigation works.
        await session.navigate("data:text/html,<title>after</title>")
        assert await session.page.title() == "after"
    finally:
        await pool.shutdown()


class _SlowImage(BaseHTTPRequestHandler):
    """``/page`` commits at once and never finishes loading: its image never answers."""

    def do_GET(self) -> None:
        srv: Any = self.server
        if self.path == "/hang.png":
            srv.release.wait(60)
            return
        body = b'<!doctype html><title>slow</title><img src="/hang.png">'
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def slow_page() -> Iterator[str]:
    srv: Any = ThreadingHTTPServer(("127.0.0.1", 0), _SlowImage)
    srv.daemon_threads = True
    srv.release = threading.Event()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/page"
    srv.release.set()
    srv.shutdown()


#: The load timeouts tried, shortest first. The case needs the commit to land
#: before the timeout fires, and that is not a property of the code under test:
#: a slow runner (a Windows leg, firefox under block-private) has taken longer
#: than 2s to commit, the goto timed out on ``about:blank``,
#: and recovery correctly reported a page that was not yet at its last URL.
_LOAD_TIMEOUTS_MS = (2_000, 10_000)


def _note_commit_order(monkeypatch: pytest.MonkeyPatch, url: str) -> list[str]:
    """Record, in the order Python sees them, the commit of *url* and the end of the latest goto to it.

    Both reach recovery over the same driver connection, so when ``commit``
    comes first, ``page.url`` was already *url* by the time recovery read it.
    Only the latest goto's page counts: an earlier replacement can still
    commit late, after the next attempt has begun.
    """
    order: list[str] = []
    latest: list[Page] = []
    original = Page.goto

    async def goto(self: Page, target: str, **kwargs: Any) -> Any:
        if target != url:
            return await original(self, target, **kwargs)
        latest[:] = [self]
        order.clear()
        main = self.main_frame
        self.on("framenavigated", lambda frame: order.append("commit") if frame is main and latest == [self] else None)
        try:
            return await original(self, target, **kwargs)
        finally:
            order.append("goto_ended")

    monkeypatch.setattr(Page, "goto", goto)
    return order


@pytest.mark.parametrize("policy", ["off", "block-private"])
async def test_a_load_that_times_out_at_the_last_url_is_a_recovery_at_it(
    kind: str, policy: str, slow_page: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", policy)
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "127.0.0.1")
    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    order = _note_commit_order(monkeypatch, slow_page)
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        try:
            inst = await pool.launch(kind=kind, headed=False, url="data:text/html,<title>start</title>")
        except Exception as exc:  # engine not installed on this host
            pytest.skip(f"{kind} unavailable: {exc}")
        session = pool.get(inst["instance_id"])
        for timeout_ms in _LOAD_TIMEOUTS_MS:
            incidents.reset()
            events.clear()
            assert await crash_recovery._recover(session, session.page, timeout_ms, slow_page) is True
            if order[:2] == ["commit", "goto_ended"]:
                break
            # Timed out before the commit: a page not yet at its last URL,
            # which is not this case. Try again with more time.
        else:
            pytest.fail(f"the commit never preceded a load timeout of up to {timeout_ms}ms: {order}")
        assert session.page.url == slow_page
        (incident,) = incidents.recent(category=incidents.CATEGORY_RENDERER_CRASH)
        assert incident["outcome"] == "recovered" and "Timeout" in incident["navigation_error"]
        assert incident["recovered_elsewhere"] is False
        (event,) = [e for e in events if getattr(e, "outcome", None) == "recovered"]
        assert event.recovered_elsewhere is False and event.navigation_error == incident["navigation_error"]
    finally:
        await pool.shutdown()


#: A URL whose load crashes the renderer, per engine (measured, Playwright 1.62).
#: Chromium's goto raises ``net::ERR_ABORTED`` a beat BEFORE the crash event;
#: Firefox's raises ``Page crashed`` after it, with the page closed or not yet.
#: WebKit has none: ``about:crash`` just times out.
_CRASHING_URL = {"chromium": "chrome://crash", "firefox": "about:crashcontent"}


async def test_a_replacement_that_keeps_crashing_is_exhausted_within_the_bound(
    kind: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    if kind not in _CRASHING_URL:
        pytest.skip(f"no URL is known to crash a {kind} renderer on load")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "off")
    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        try:
            inst = await pool.launch(kind=kind, headed=False, url="data:text/html,<title>start</title>")
        except Exception as exc:  # engine not installed on this host
            pytest.skip(f"{kind} unavailable: {exc}")
        session = pool.get(inst["instance_id"])
        incidents.reset()
        dead = session.page
        session._crashed = True
        session._last_crash_monotonic = time.monotonic()  # as schedule_recovery stamps it
        assert await crash_recovery._recover(session, dead, 15_000, _CRASHING_URL[kind]) is False
        assert session._crash_recoveries == defaults.CRASH_RECOVERY_MAX
        assert session.page is dead and session.pages == [dead]
        assert session._crashed is True
        # Nothing else is left to run: the replacement's own crash scheduled no second recovery.
        await asyncio.sleep(1)
        assert not [t for t in session._bg_tasks if not t.done()]
        assert [e.outcome for e in events if type(e).__name__ == "SessionRecoveredEvent"] == ["exhausted"]
        assert [i["outcome"] for i in incidents.recent(category=incidents.CATEGORY_RENDERER_CRASH)] == ["exhausted"]
        assert all(page.is_closed() for page in session.context.pages if page is not dead)
    finally:
        await pool.shutdown()
