# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Under ``block-private`` the real redirect chain is still on record.

The guard answers a redirect with a client-redirect document, so the browser
sees a 200 for every hop but the last. It tags each such request with the
3xx it actually got; the session's ``response`` listener reads that tag, so
``browser_network_requests`` shows the chain. That join is by request object,
so this proves on each engine that the route's request and the ``response``
event's request are the same object.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.ssrf_guard import client_redirect_of
from tests.ssrf_guard import test_subresource_and_toctou_live as _served

pytestmark = pytest.mark.live_browser

# The redirecting fixture server and a guarded context on each engine.
server = _served.server
context = _served.context
_base = _served._base


async def test_each_hop_of_a_chain_carries_its_real_redirect(server: Any, context: Any) -> None:
    page = await context.new_page()
    seen: list[tuple[str, int, dict[str, Any] | None]] = []
    page.on("response", lambda r: seen.append((r.url, r.status, client_redirect_of(r.request))))
    await page.goto(f"{_base(server)}/d1/a")
    await page.wait_for_url("**/d3/c")
    await page.wait_for_load_state("load")
    by_path = {url.removeprefix(_base(server)): (status, tag) for url, status, tag in seen}
    assert by_path["/d1/a"] == (200, {"status": 302, "status_text": "Found", "location": f"{_base(server)}/d2/b"})
    assert by_path["/d2/b"][1] == {"status": 302, "status_text": "Found", "location": f"{_base(server)}/d3/c"}
    assert by_path["/d3/c"] == (200, None)


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def pooled_session(
    request: pytest.FixtureRequest, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, server: Any
) -> Any:
    from octowright.browser_pool.pool import BrowserPool

    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "127.0.0.1")
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        inst = await pool.launch(kind=request.param, headed=False, url=f"{_base(server)}/page")
    except Exception as exc:  # engine not installed on this host
        await pool.shutdown()
        pytest.skip(f"{request.param} unavailable: {exc}")
    try:
        yield pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


async def test_browser_network_requests_shows_the_chain(server: Any, pooled_session: Any) -> None:
    await pooled_session.navigate(f"{_base(server)}/d1/a")
    await pooled_session.page.wait_for_url("**/d3/c")
    rows = pooled_session.get_network_requests(resource_type_filter="document")["requests"]
    chain = [(r["url"].removeprefix(_base(server)), r["status"], r.get("redirect_location")) for r in rows]
    assert ("/d1/a", 302, f"{_base(server)}/d2/b") in chain, chain
    assert ("/d2/b", 302, f"{_base(server)}/d3/c") in chain, chain
    assert ("/d3/c", 200, None) in chain, chain
