# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Renderer-crash auto-recovery (browser_pool.crash_recovery).

A Playwright page.on("crash") leaves the browser process alive with a dead
renderer; page.reload() heals it. These tests cover eligibility (cap +
crash-loop reset), the async reload path (success/failure), and the readable
stats surfaced in octowright_status.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from octowright.browser_pool import crash_recovery, incidents


def _session(*, recoveries: int = 0, last_crash: float = 0.0) -> SimpleNamespace:
    dead_page = MagicMock(name="dead_page")
    dead_page.url = "https://example.com"
    dead_page.close = AsyncMock()
    fresh_page = MagicMock(name="fresh_page")
    fresh_page.goto = AsyncMock()
    fresh_page.screenshot = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=fresh_page)
    return SimpleNamespace(
        instance_id="abc123",
        kind="chromium",
        label=None,
        profile=None,
        log_path=Path("/tmp/x.jsonl"),
        url="https://example.com",
        _crashed=True,
        _crash_recoveries=recoveries,
        _last_crash_monotonic=last_crash,
        _bg_tasks=set(),
        recorder=MagicMock(),
        context=context,
        page=dead_page,
        pages=[dead_page],
        page_count=1,
    )


@pytest.fixture(autouse=True)
def _reset_stats() -> None:
    crash_recovery.reset_stats()
    incidents.reset()


@pytest.fixture(autouse=True)
def _stub_wire_listeners(monkeypatch: pytest.MonkeyPatch) -> None:
    # _replace_crashed_page rewires the fresh page via listeners._wire_listeners,
    # which needs a real session/context; stub it out for these unit tests.
    import octowright.browser_pool.listeners as _listeners

    monkeypatch.setattr(_listeners, "_wire_listeners", lambda *_a, **_k: None)


def test_note_crash_increments_stats() -> None:
    crash_recovery.note_crash()
    crash_recovery.note_crash()
    assert crash_recovery.recovery_stats()["crashes"] == 2


def test_eligible_resets_counter_after_quiet_period() -> None:
    s = _session(recoveries=99, last_crash=100.0)
    # now is far past last_crash → not a crash loop → counter resets, eligible again
    assert crash_recovery._eligible(s, max_recoveries=3, reset_seconds=60.0, now=1000.0) is True
    assert s._crash_recoveries == 0
    assert s._last_crash_monotonic == 1000.0


def test_eligible_false_when_cap_hit_within_window() -> None:
    s = _session(recoveries=3, last_crash=1000.0)
    # crash 1s later → same loop, counter NOT reset → cap (3) reached → ineligible
    assert crash_recovery._eligible(s, max_recoveries=3, reset_seconds=60.0, now=1001.0) is False
    assert s._crash_recoveries == 3


def test_safe_url_prefers_page_url_then_session() -> None:
    s = _session()
    good = MagicMock()
    good.url = "https://live.example/page"
    assert crash_recovery._safe_url(good, s) == "https://live.example/page"
    crashed = MagicMock()
    type(crashed).url = PropertyMock(side_effect=RuntimeError("url on crashed page"))
    assert crash_recovery._safe_url(crashed, s) == "https://example.com"  # falls back to session.url


async def test_recover_publishes_recovered_event(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import session_event_bus as _bus

    events: list = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    assert len(events) == 1
    assert events[0].outcome == "recovered"
    assert events[0].instance_id == "abc123"


async def test_recover_failure_publishes_failed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import session_event_bus as _bus

    events: list = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    s.context.new_page.return_value.goto = AsyncMock(side_effect=RuntimeError("Target closed"))
    await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    assert len(events) == 1 and events[0].outcome == "failed"


async def test_recover_replaces_dead_page_and_records_incident() -> None:
    s = _session()
    dead = s.page
    fresh = s.context.new_page.return_value
    ok = await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com")
    assert ok is True
    assert s._crashed is False
    assert s._crash_recoveries == 1
    assert crash_recovery.recovery_stats()["recoveries"] == 1
    # A fresh page in the surviving context replaced the dead one, navigated to
    # its URL, and became the session's active page; the dead page was closed.
    s.context.new_page.assert_awaited_once()
    fresh.goto.assert_awaited_once_with("https://example.com", timeout=15000.0)
    assert s.page is fresh
    assert s.pages == [fresh]
    dead.close.assert_awaited_once()
    s.recorder.record.assert_called_once()
    # An incident record with outcome="recovered" is now visible in status,
    # including a postmortem screenshot path (H5a).
    inc = incidents.recent(category="renderer_crash")
    assert len(inc) == 1
    assert inc[0]["outcome"] == "recovered"
    assert inc[0]["instance_id"] == "abc123"
    assert inc[0]["url"] == "https://example.com"
    fresh.screenshot.assert_awaited_once()
    # Compare as Path so the assertion is separator-agnostic (Windows renders the
    # same path with backslashes); the product builds it from log_path via pathlib.
    assert Path(inc[0]["screenshot"]) == Path("/tmp/x.recovery-1.png")


async def test_recovery_screenshot_failure_does_not_break_recovery() -> None:
    s = _session()
    s.context.new_page.return_value.screenshot = AsyncMock(side_effect=RuntimeError("screenshot dead"))
    ok = await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    assert ok is True  # screenshot is best-effort; recovery still succeeds
    assert incidents.recent(category="renderer_crash")[0]["screenshot"] is None


async def test_recovered_incident_visible_before_slow_screenshot(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _session()
    screenshot_started = asyncio.Event()
    release_screenshot = asyncio.Event()

    async def slow_screenshot(_session: object) -> str:
        screenshot_started.set()
        await release_screenshot.wait()
        return "/tmp/later.png"

    monkeypatch.setattr(crash_recovery, "_capture_recovery_screenshot", slow_screenshot)

    task = asyncio.create_task(crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com"))
    await screenshot_started.wait()

    inc = incidents.recent(category="renderer_crash")
    assert len(inc) == 1
    assert inc[0]["outcome"] == "recovered"
    assert inc[0]["screenshot"] is None
    assert crash_recovery.recovery_stats()["recoveries"] == 1

    release_screenshot.set()
    assert await task is True
    assert inc[0]["screenshot"] == "/tmp/later.png"


async def test_recover_succeeds_even_if_recorder_marker_fails() -> None:
    s = _session()
    s.recorder.record.side_effect = RuntimeError("recorder closed")
    ok = await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    # Recovery succeeded; the best-effort recorder marker failure is swallowed.
    assert ok is True
    assert s._crashed is False
    assert crash_recovery.recovery_stats()["recoveries"] == 1


async def test_recover_succeeds_even_if_dead_page_close_fails() -> None:
    s = _session()
    s.page.close = AsyncMock(side_effect=RuntimeError("page already gone"))
    ok = await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    # The crashed page often can't be closed; that's swallowed, recovery still wins.
    assert ok is True
    assert s._crashed is False


async def test_recover_foreign_page_appends_without_swap() -> None:
    # Defensive branches: the dead page is neither in session.pages nor the active
    # page (append, no swap).
    s = _session()
    foreign = MagicMock(name="foreign_dead_page")
    foreign.url = "https://example.com"
    foreign.close = AsyncMock()
    ok = await crash_recovery._recover(s, foreign, reload_timeout_ms=15000.0, url="https://example.com")
    assert ok is True
    fresh = s.context.new_page.return_value
    fresh.goto.assert_awaited_once_with("https://example.com", timeout=15000.0)
    assert fresh in s.pages  # foreign page wasn't in pages → appended
    assert s.page is not fresh  # foreign page wasn't the active page → no swap


async def test_recover_failure_records_failed_incident() -> None:
    s = _session()
    s.context.new_page.return_value.goto = AsyncMock(side_effect=RuntimeError("Target closed"))
    ok = await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    assert ok is False
    assert s._crashed is True  # left crashed → LLM sees "relaunch"
    assert crash_recovery.recovery_stats()["recovery_failures"] == 1
    inc = incidents.recent(category="renderer_crash")
    assert len(inc) == 1 and inc[0]["outcome"] == "failed"


def test_schedule_recovery_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults

    monkeypatch.setattr(defaults, "CRASH_RECOVERY_ENABLED", False)
    assert crash_recovery.schedule_recovery(_session(), MagicMock()) is None


def test_schedule_recovery_exhausted_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults

    monkeypatch.setattr(defaults, "CRASH_RECOVERY_ENABLED", True)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_MAX", 3)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_RESET_SECONDS", 60.0)
    # recoveries already at the cap, last crash "just now" so no reset
    s = _session(recoveries=3, last_crash=1e9)

    async def _run() -> None:
        # inside a loop so get_running_loop() succeeds; should still skip on cap
        assert crash_recovery.schedule_recovery(s, MagicMock()) is None

    asyncio.run(_run())


def test_schedule_recovery_no_running_loop_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults

    monkeypatch.setattr(defaults, "CRASH_RECOVERY_ENABLED", True)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_MAX", 3)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_RESET_SECONDS", 60.0)
    # Called outside any event loop: eligible, but nothing to schedule on → None.
    assert crash_recovery.schedule_recovery(_session(), MagicMock()) is None


def test_schedule_recovery_eligible_creates_tracked_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults

    monkeypatch.setattr(defaults, "CRASH_RECOVERY_ENABLED", True)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_MAX", 3)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_RESET_SECONDS", 60.0)
    monkeypatch.setattr(defaults, "CRASH_RECOVERY_RELOAD_TIMEOUT_MS", 15000.0)
    s = _session()

    async def _run() -> None:
        task = crash_recovery.schedule_recovery(s, s.page)
        assert task is not None
        assert task in s._bg_tasks
        await task
        assert task not in s._bg_tasks  # done-callback discards it
        s.context.new_page.assert_awaited_once()  # recovery replaced the dead page

    asyncio.run(_run())
