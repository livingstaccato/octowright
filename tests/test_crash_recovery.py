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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from octowright.browser_pool import crash_recovery


def _session(*, recoveries: int = 0, last_crash: float = 0.0) -> SimpleNamespace:
    dead_page = MagicMock(name="dead_page")
    dead_page.url = "https://example.com"
    dead_page.close = AsyncMock()
    fresh_page = MagicMock(name="fresh_page")
    fresh_page.goto = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=fresh_page)
    return SimpleNamespace(
        instance_id="abc123",
        kind="chromium",
        label=None,
        profile=None,
        log_path="/tmp/x.jsonl",
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


async def test_recover_replaces_dead_page_and_clears_crashed() -> None:
    s = _session()
    dead = s.page
    fresh = s.context.new_page.return_value
    ok = await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0)
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


async def test_recover_succeeds_even_if_recorder_marker_fails() -> None:
    s = _session()
    s.recorder.record.side_effect = RuntimeError("recorder closed")
    ok = await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0)
    # Recovery succeeded; the best-effort recorder marker failure is swallowed.
    assert ok is True
    assert s._crashed is False
    assert crash_recovery.recovery_stats()["recoveries"] == 1


async def test_recover_succeeds_even_if_dead_page_close_fails() -> None:
    s = _session()
    s.page.close = AsyncMock(side_effect=RuntimeError("page already gone"))
    ok = await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0)
    # The crashed page often can't be closed; that's swallowed, recovery still wins.
    assert ok is True
    assert s._crashed is False


async def test_recover_handles_unreadable_url_and_foreign_page() -> None:
    # Defensive branches: the crashed page's .url raises (fall back to session.url),
    # and the dead page is neither in session.pages nor the active page (append, no swap).
    s = _session()
    foreign = MagicMock(name="foreign_dead_page")
    type(foreign).url = PropertyMock(side_effect=RuntimeError("url on crashed page"))
    foreign.close = AsyncMock()
    ok = await crash_recovery._recover(s, foreign, reload_timeout_ms=15000.0)
    assert ok is True
    fresh = s.context.new_page.return_value
    fresh.goto.assert_awaited_once_with("https://example.com", timeout=15000.0)  # fell back to session.url
    assert fresh in s.pages  # foreign page wasn't in pages → appended
    assert s.page is not fresh  # foreign page wasn't the active page → no swap


async def test_recover_failure_when_new_page_navigation_fails() -> None:
    s = _session()
    s.context.new_page.return_value.goto = AsyncMock(side_effect=RuntimeError("Target closed"))
    ok = await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0)
    assert ok is False
    assert s._crashed is True  # left crashed → LLM sees "relaunch"
    assert crash_recovery.recovery_stats()["recovery_failures"] == 1


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
