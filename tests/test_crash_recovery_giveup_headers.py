# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""When crash recovery gives up on a page, its page-header record goes with it.

Page-level headers are remembered per page (``session/page_headers``), and a
closed page's are forgotten. A crashed page is not closed, though -- measured
on headless Chromium (Playwright 1.62): after ``Page.crash`` the page reports
``is_closed() == False`` and stays in ``context.pages`` until something closes
it. So when recovery ended ``exhausted`` (or ``failed``) on a page that was not
the active one, its headers stayed on record, and every later replacement
warned that headers "set on a page other than the active one" were not carried
-- for a page that was dead. The record is now dropped when recovery gives up,
and the crash incident says so. A crashed page that is still the active one
keeps its record: a later replacement reopens the active page, so carrying
its headers is still right.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool import crash_recovery, incidents
from octowright.session.page_headers import page_headers_on, set_page_headers
from octowright.session.route_carry import RouteCarry
from tests._operation_gate_fakes import OperationAwareFake

HEADERS = {"X-Fixture": "Fixture-Not-A-Real-Secret-g11"}


class _Session(OperationAwareFake):
    context: MagicMock

    def __init__(self, **attrs: Any) -> None:
        self.instance_id = "g11"
        self.kind = "chromium"
        super().__init__()
        for key, value in attrs.items():
            setattr(self, key, value)


def _page(name: str) -> MagicMock:
    page = MagicMock(name=name)
    page.url = "https://example.com"
    page.is_closed = MagicMock(return_value=False)  # a crashed page is not closed
    page.close = AsyncMock()
    return page


def _session(*, crashed_is_active: bool, recoveries: int = 0) -> tuple[_Session, MagicMock]:
    crashed = _page("crashed")
    other = _page("other")
    s = _Session(
        label=None,
        profile=None,
        log_path=Path("/tmp/x.jsonl"),
        url="https://example.com",
        _crashed=True,
        _crash_recoveries=recoveries,
        _last_crash_monotonic=time.monotonic(),
        _bg_tasks=set(),
        recorder=MagicMock(),
        context=MagicMock(),
        page=crashed if crashed_is_active else other,
        pages=[other, crashed],
        page_count=2,
        _page_extra_headers_by_page=[],
    )
    set_page_headers(s, crashed, HEADERS)
    return s, crashed


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.browser_pool.listeners as _listeners
    from octowright import defaults
    from octowright.browser_pool import session_event_bus as _bus

    crash_recovery.reset_stats()
    incidents.reset()
    monkeypatch.setattr(_listeners, "_wire_listeners", lambda *_a, **_k: None)
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", lambda *_a: None)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_ENABLED", True)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_MAX", 2)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_RESET_SECONDS", 60.0)


def _headers_warning(s: _Session) -> list[str]:
    return [w for w in RouteCarry.of(s).not_carried if "page-level extra HTTP headers" in w]


def _incident() -> dict[str, Any]:
    (inc,) = incidents.recent(category="renderer_crash")
    return inc


async def test_exhausted_before_any_attempt_drops_a_background_pages_headers() -> None:
    s, crashed = _session(crashed_is_active=False, recoveries=2)

    assert crash_recovery.schedule_recovery(s, crashed) is None

    assert page_headers_on(s, crashed) is None
    assert _headers_warning(s) == []
    inc = _incident()
    assert inc["outcome"] == "exhausted"
    assert any("crashed page" in w for w in inc["route_warnings"])
    assert HEADERS["X-Fixture"] not in repr(inc)


async def test_exhausted_after_failing_replacements_drops_a_background_pages_headers() -> None:
    s, crashed = _session(crashed_is_active=False)

    async def crashing_goto(*_a: Any, **_k: Any) -> None:
        assert crash_recovery.claim_replacement_crash(fresh) is True
        raise RuntimeError("Page.goto: Page crashed")

    fresh = _page("fresh")
    fresh.url = "about:blank"
    fresh.goto = crashing_goto
    fresh.set_extra_http_headers = AsyncMock()
    s.context.new_page = AsyncMock(return_value=fresh)

    assert await crash_recovery._recover(s, crashed, reload_timeout_ms=1000.0, url="https://example.com") is False

    assert page_headers_on(s, crashed) is None
    assert _headers_warning(s) == []
    inc = _incident()
    assert inc["outcome"] == "exhausted"
    assert any("crashed page" in w for w in inc["route_warnings"])


async def test_failed_recovery_drops_a_background_pages_headers() -> None:
    s, crashed = _session(crashed_is_active=False)
    s.context.new_page = AsyncMock(side_effect=RuntimeError("context gone"))

    assert await crash_recovery._recover(s, crashed, reload_timeout_ms=1000.0, url="https://example.com") is False

    assert page_headers_on(s, crashed) is None
    assert _incident()["outcome"] == "failed"
    assert any("crashed page" in w for w in _incident()["route_warnings"])


async def test_the_active_crashed_page_keeps_its_headers_for_a_later_replacement() -> None:
    s, crashed = _session(crashed_is_active=True, recoveries=2)

    assert crash_recovery.schedule_recovery(s, crashed) is None

    assert page_headers_on(s, crashed) == HEADERS
    assert RouteCarry.of(s).page_headers == HEADERS
    assert "route_warnings" not in _incident()
