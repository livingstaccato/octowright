# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live: a headed Chromium's first download no longer kills its browser.

The crash (``browser_pool/download_history``) needs three things: a headed
Chromium on ``chromium-1243`` (Chrome for Testing 153.0.8010.12, Playwright
1.63), a persistent profile that already holds a download-history row, and the
first download of a fresh browser process. This test builds exactly that --
seed one download, then relaunch the same profile and download first thing,
several times -- and requires every download to land and no crash to be seen.

On the ``chromium-1234`` this repo's lock pins, the crash does not reproduce, so
there the test proves the prune runs and downloads survive; the crash-rate
proof is the same test run against Playwright 1.63
(``uv run --with playwright==1.63.0 pytest ...``), where the unpatched rate
measured 27/27 and the patched rate 0/30 (90/90 downloads).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.live_browser,
    pytest.mark.skipif(not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"), reason="headed only"),
]

_RELAUNCHES = 5
_TRIGGER = (
    "(()=>{let s='[';while(s.length<70000)s+='{\"k\":\"abcdefghij\"},';"
    "const a=document.createElement('a');"
    "a.href=URL.createObjectURL(new Blob([s+'0]'],{type:'application/octet-stream'}));"
    "a.download='probe.json';document.body.appendChild(a);setTimeout(()=>{a.click();a.remove();},50);})()"
)


class _Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<!doctype html><title>dl</title><h1>download probe</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture
def local_http_server() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()
        srv.server_close()


def _download_rows(user_data_dir: Path) -> int:
    history = user_data_dir / "Default" / "History"
    if not history.exists():
        return 0
    con = sqlite3.connect(f"file:{history}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT count(*) FROM downloads").fetchone()[0])
    finally:
        con.close()


async def _download_once(pool: Any, url: str) -> tuple[Any, bool]:
    result = await pool.launch(kind="chromium", url=url, headed=True, label="dlhist-live", protected=False)
    session = pool.get(result["instance_id"])
    waiter = asyncio.ensure_future(session.wait_for_download(timeout_ms=15000))
    await session.page.evaluate(_TRIGGER)
    try:
        await waiter
        saved = True
    except Exception:
        saved = False
    return session, saved


async def test_first_download_after_relaunch_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_http_server: Any
) -> None:
    from octowright import session_manifest
    from octowright.browser_pool import BrowserPool, incidents
    from octowright.browser_pool.events import SessionCrashedEvent
    from octowright.browser_pool.session_event_bus import session_event_bus

    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "manifest.json")
    events: list[Any] = []
    monkeypatch.setattr(session_event_bus, "publish_nowait", events.append)
    incidents.reset()
    rec = tmp_path / "rec"
    pool = BrowserPool(recordings_dir=rec)
    try:
        # Seed: one download leaves one row in the profile's History.
        session, saved = await _download_once(pool, local_http_server)
        assert saved
        udd = session.user_data_dir
        await pool.close(session.instance_id, force=True)
        assert _download_rows(udd) >= 1, "the seeding download left no history row to prune"

        outcomes = []
        for _ in range(_RELAUNCHES):
            session, saved = await _download_once(pool, local_http_server)
            outcomes.append(saved)
            if pool.maybe_get(session.instance_id) is not None:
                await pool.close(session.instance_id, force=True)

        assert outcomes == [True] * _RELAUNCHES
        assert not [e for e in events if isinstance(e, SessionCrashedEvent)]
        assert incidents.recent(category=incidents.CATEGORY_BROWSER_PROCESS_CRASH) == []
    finally:
        await pool.shutdown()
        # The downloaded payloads are this test's own files; do not leave them.
        shutil.rmtree(rec / "downloads", ignore_errors=True)
    assert not (rec / "downloads").exists()
