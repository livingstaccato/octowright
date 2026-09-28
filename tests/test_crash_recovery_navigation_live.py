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
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

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
