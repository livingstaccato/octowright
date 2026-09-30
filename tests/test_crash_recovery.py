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
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from octowright.browser_pool import crash_recovery, incidents
from octowright.session.operation.gate import SessionOperationGate
from tests._operation_gate_fakes import OperationAwareFake


class _FakeCrashSession(OperationAwareFake):
    """Minimal session-shaped fake for crash-recovery tests: a real gate
    (``_recover`` is ``async with session.operation("crash_recovery", ...)``)
    plus whatever page/context/bookkeeping attributes the caller supplies."""

    def __init__(self, **attrs: Any) -> None:
        self.instance_id = attrs.pop("instance_id", "abc123")
        self.kind = attrs.pop("kind", "chromium")
        super().__init__()
        for key, value in attrs.items():
            setattr(self, key, value)


def _session(*, recoveries: int = 0, last_crash: float = 0.0) -> _FakeCrashSession:
    dead_page = MagicMock(name="dead_page")
    dead_page.url = "https://example.com"
    dead_page.close = AsyncMock()
    fresh_page = MagicMock(name="fresh_page")
    fresh_page.url = "https://example.com"  # where a goto that succeeded leaves it
    fresh_page.goto = AsyncMock()
    fresh_page.screenshot = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=fresh_page)
    return _FakeCrashSession(
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


async def test_replace_crashed_page_no_duplicate_when_page_event_already_appended() -> None:
    """Real Playwright fires the context 'page' event for ``context.new_page()``,
    so ``_register_popup`` has already appended the fresh page by the time
    recovery runs. Recovery must not append/replace it a SECOND time — the fresh
    page must appear exactly once and the dead page must be gone (else the pages
    list carries a duplicate and page_count is wrong)."""
    dead = MagicMock(name="dead")
    dead.url = "https://example.com"
    dead.close = AsyncMock()
    fresh = MagicMock(name="fresh")
    fresh.goto = AsyncMock()
    fresh.screenshot = AsyncMock()
    context = MagicMock()
    s = _FakeCrashSession(
        label=None,
        profile=None,
        log_path=Path("/tmp/x.jsonl"),
        url="https://example.com",
        _crashed=True,
        _crash_recoveries=0,
        _last_crash_monotonic=0.0,
        _bg_tasks=set(),
        recorder=MagicMock(),
        context=context,
        page=dead,
        pages=[dead],
        page_count=1,
    )

    async def _new_page_fires_popup() -> MagicMock:
        # Mimic _register_popup appending on the context 'page' event.
        s.pages.append(fresh)
        s.page_count = len(s.pages)
        return fresh

    context.new_page = AsyncMock(side_effect=_new_page_fires_popup)

    ok = await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com")

    assert ok is True
    assert s.pages == [fresh]
    assert s.page is fresh
    assert s.page_count == 1
    dead.close.assert_awaited_once()


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
    s.context.new_page.return_value.is_closed = MagicMock(return_value=True)
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
    s.context.new_page.return_value.is_closed = MagicMock(return_value=True)
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


# ---------------------------------------------------------------------------
# operation-gate serialization: crash_recovery as a durable system
# operation with no ordinary queue timeout, invalidated by external close.
# ---------------------------------------------------------------------------


async def _wait_for_queue_depth(gate: SessionOperationGate, depth: int) -> None:
    async with asyncio.timeout(1):
        while gate.snapshot()["queue_depth"] != depth:
            await asyncio.sleep(0)


async def test_recover_waits_behind_the_operation_that_crashed_ignoring_ordinary_timeout() -> None:
    """``_recover`` uses ``wait_timeout_seconds=None`` -- it must keep waiting
    behind whatever operation owns the gate even well past the gate's
    ordinary (short, here) queue timeout, rather than raising
    SessionBusyTimeoutError like a normal operation would."""
    s = _session()
    s._test_operation_gate = SessionOperationGate(s.instance_id, s.kind, queue_timeout_seconds=0.05)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold() -> None:
        async with s.operation("browser_click"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await entered.wait()

    recover_task = asyncio.create_task(
        crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    )
    # Well past the 0.05s ordinary queue timeout -- an ordinarily-gated
    # operation would have raised SessionBusyTimeoutError by now.
    await asyncio.sleep(0.2)
    assert not recover_task.done()
    s.context.new_page.assert_not_awaited()

    release.set()
    await holder
    ok = await recover_task
    assert ok is True
    s.context.new_page.assert_awaited_once()


async def test_recover_invalidated_by_external_close_does_not_touch_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovery ticket still queued when the session closes externally must
    be invalidated (return False, log, no page replacement, no recovered/
    failed event) rather than retried or run anyway."""
    from octowright.browser_pool import session_event_bus as _bus

    events: list = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)

    s = _session()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _hold() -> None:
        async with s.operation("browser_click"):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await entered.wait()

    recover_task = asyncio.create_task(
        crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    )
    await _wait_for_queue_depth(s._test_operation_gate, 1)

    s._test_operation_gate.mark_closed_external()
    release.set()
    await holder

    ok = await recover_task
    assert ok is False
    s.context.new_page.assert_not_awaited()
    assert events == []  # neither "recovered" nor "failed" published
    assert crash_recovery.recovery_stats()["recoveries"] == 0
    assert crash_recovery.recovery_stats()["recovery_failures"] == 0


async def test_second_concurrent_recovery_queues_behind_the_first() -> None:
    """Two crash callbacks racing the same session (e.g. a second renderer
    crash while the first recovery is in flight) must serialize through the
    gate rather than both replacing the page concurrently."""
    s = _session()
    first_release = asyncio.Event()

    async def _slow_goto(_url: str, timeout: float) -> None:
        await first_release.wait()

    s.context.new_page.return_value.goto = AsyncMock(side_effect=_slow_goto)

    first_task = asyncio.create_task(
        crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    )
    # Let the first attempt actually enter goto() before starting the second.
    async with asyncio.timeout(1):
        while s.context.new_page.await_count == 0:
            await asyncio.sleep(0)

    second_task = asyncio.create_task(
        crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    )
    await _wait_for_queue_depth(s._test_operation_gate, 1)
    assert s.context.new_page.await_count == 1  # second has not started yet

    first_release.set()
    ok1 = await first_task
    ok2 = await second_task
    assert ok1 is True
    assert ok2 is True
    assert s.context.new_page.await_count == 2


async def test_recovery_onto_a_refused_url_recovers_and_reports_the_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """It raised after the new page was wired: the new page was orphaned and the dead one stayed active."""
    from octowright import ssrf_guard

    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    s = _session()
    dead = s.page
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=False)

    async def refused_goto(*_args: Any, **_kwargs: Any) -> None:
        ssrf_guard.frame_chain(fresh.main_frame).end("redirect to 'http://169.254.169.254/' refused", failed=False)

    fresh.goto = refused_goto
    ok = await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com")
    assert ok is True
    assert s._crashed is False
    assert s.page is fresh and s.pages == [fresh]
    dead.close.assert_awaited_once()
    (inc,) = incidents.recent(category="renderer_crash")
    assert inc["outcome"] == "recovered"
    assert "169.254.169.254" in inc["navigation_error"]


async def test_recovery_onto_a_url_that_fails_to_load_recovers_and_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed fetch raised after the new page was wired: it was orphaned and the dead page stayed active."""
    from octowright import ssrf_guard

    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    s = _session()
    dead = s.page
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=False)

    async def failed_goto(*_args: Any, **_kwargs: Any) -> None:
        ssrf_guard.frame_chain(fresh.main_frame).end("connection reset", failed=True)

    fresh.goto = failed_goto
    ok = await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com")
    assert ok is True
    assert s._crashed is False
    assert s.page is fresh and s.pages == [fresh]
    dead.close.assert_awaited_once()
    (inc,) = incidents.recent(category="renderer_crash")
    assert inc["outcome"] == "recovered" and "connection reset" in inc["navigation_error"]


@pytest.mark.parametrize(
    "error",
    [RuntimeError("Page.goto: net::ERR_CONNECTION_REFUSED"), TimeoutError("Page.goto: Timeout 15000ms exceeded.")],
)
async def test_recovery_with_the_policy_off_recovers_past_a_failed_navigation(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """The same with no policy: the page swap finishes and the failure is reported, not raised."""
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    s = _session()
    dead = s.page
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=False)
    fresh.goto = AsyncMock(side_effect=error)
    ok = await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com")
    assert ok is True
    assert s._crashed is False
    assert s.page is fresh and s.pages == [fresh] and s.page_count == 1
    dead.close.assert_awaited_once()
    (inc,) = incidents.recent(category="renderer_crash")
    assert inc["outcome"] == "recovered" and str(error) in inc["navigation_error"]


async def test_recovery_fails_without_orphaning_a_replacement_that_closed() -> None:
    """A replacement that died with its navigation is not swapped in, and is not left in the page list."""
    s = _session()
    dead = s.page
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=True)

    async def closing_goto(*_args: Any, **_kwargs: Any) -> None:
        s.pages.append(fresh)  # the context "page" event registered it first
        raise RuntimeError("Page.goto: Target page, context or browser has been closed")

    fresh.goto = closing_goto
    ok = await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com")
    assert ok is False
    assert s._crashed is True
    assert s.page is dead and s.pages == [dead] and s.page_count == 1
    (inc,) = incidents.recent(category="renderer_crash")
    assert inc["outcome"] == "failed"


async def test_a_recovery_elsewhere_is_published_with_its_navigation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """It was published as a plain 'recovered' before the failure was known, saying nothing about the URL."""
    from octowright.browser_pool import session_event_bus as _bus

    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=False)
    fresh.url = "chrome-error://chromewebdata/"
    fresh.goto = AsyncMock(side_effect=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))

    def incident_recorded_first(event: Any) -> None:
        (inc,) = incidents.recent(category="renderer_crash")
        assert inc.get("navigation_error") == event.navigation_error
        events.append(event)

    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", incident_recorded_first)
    await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    (event,) = events
    assert event.outcome == "recovered"
    assert event.recovered_elsewhere is True
    assert "ERR_NAME_NOT_RESOLVED" in event.navigation_error


async def test_a_recovery_at_the_last_url_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import session_event_bus as _bus

    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com")
    (event,) = events
    assert event.outcome == "recovered" and event.recovered_elsewhere is False and event.navigation_error is None


@pytest.mark.parametrize("page_url", ["https://example.com", "https://EXAMPLE.com:443/#top"])
async def test_a_slow_load_at_the_last_url_is_a_recovery_at_it(monkeypatch: pytest.MonkeyPatch, page_url: str) -> None:
    """A load timeout AFTER the navigation committed was reported as "NOT at its last URL"."""
    from octowright.browser_pool import session_event_bus as _bus

    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=False)
    fresh.url = page_url
    fresh.goto = AsyncMock(side_effect=TimeoutError("Page.goto: Timeout 15000ms exceeded."))
    assert await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com") is True
    (event,) = events
    assert event.outcome == "recovered" and event.recovered_elsewhere is False
    assert "Timeout" in event.navigation_error  # still said, for information
    (inc,) = incidents.recent(category="renderer_crash")
    assert "Timeout" in inc["navigation_error"] and inc["recovered_elsewhere"] is False


def _crashing_page(s: _FakeCrashSession, name: str) -> MagicMock:
    """A replacement whose renderer crashes while it loads, as its crash listener reports it."""
    page = MagicMock(name=name)
    page.url = "about:blank"
    page.is_closed = MagicMock(return_value=False)
    page.close = AsyncMock()

    async def crashing_goto(*_args: Any, **_kwargs: Any) -> None:
        s.pages.append(page)  # the context "page" event registered it first
        # What the page's crash listener sees: a replacement this recovery owns.
        assert crash_recovery.claim_replacement_crash(page) is True
        raise RuntimeError("Page.goto: Page crashed")

    page.goto = crashing_goto
    return page


async def test_a_replacement_that_keeps_crashing_is_exhausted_within_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """It was swapped in as "recovered elsewhere" while its own crash scheduled a second recovery.

    Then it failed the whole recovery on the first crash, so the crash-loop
    bound no longer applied. It is retried, one attempt per replacement, and
    ends ``exhausted`` once ``CRASH_RECOVERY_MAX`` is spent.
    """
    import time

    from octowright import defaults
    from octowright.browser_pool import session_event_bus as _bus

    monkeypatch.setattr(defaults, "CRASH_RECOVERY_MAX", 2)
    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    s._last_crash_monotonic = time.monotonic()  # as schedule_recovery stamps it
    dead = s.page
    fresh = _crashing_page(s, "fresh")
    s.context.new_page = AsyncMock(return_value=fresh)
    assert await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com") is False
    assert s.context.new_page.await_count == 2
    assert s._crash_recoveries == 2
    assert s._crashed is True
    assert s.page is dead and s.pages == [dead] and s.page_count == 1
    assert fresh.close.await_count == 2
    (inc,) = incidents.recent(category="renderer_crash")
    assert inc["outcome"] == "exhausted"
    assert [e.outcome for e in events] == ["exhausted"]
    # A crash event arriving late for the failed replacement is still its recovery's, not a new one.
    assert crash_recovery.claim_replacement_crash(fresh) is True


async def test_a_replacement_that_crashes_once_is_replaced_again(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from octowright.browser_pool import session_event_bus as _bus

    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    s._last_crash_monotonic = time.monotonic()
    dead = s.page
    crashing = _crashing_page(s, "crashing")
    good = s.context.new_page.return_value
    s.context.new_page = AsyncMock(side_effect=[crashing, good])
    assert await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com") is True
    assert s._crashed is False and s._crash_recoveries == 2
    assert s.page is good and s.pages == [good]
    crashing.close.assert_awaited_once()
    dead.close.assert_awaited_once()
    assert [(e.outcome, e.recovered_elsewhere) for e in events] == [("recovered", False)]


async def test_a_replacement_that_crashes_while_it_is_swapped_in_is_this_recoverys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It left _REPLACEMENTS before the swap: its crash there scheduled a second recovery, and this one said "recovered" after it."""
    import time

    from octowright.browser_pool import session_event_bus as _bus

    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    s._last_crash_monotonic = time.monotonic()
    dead = s.page
    first = MagicMock(name="first")
    first.url = "https://example.com"
    first.goto = AsyncMock()
    first.close = AsyncMock()
    first.is_closed = MagicMock(return_value=False)
    second = s.context.new_page.return_value
    s.context.new_page = AsyncMock(side_effect=[first, second])
    claimed: list[bool] = []

    async def crash_in_the_window() -> None:
        # The dead page's close yields: the replacement crashes right there.
        claimed.append(crash_recovery.claim_replacement_crash(first))

    dead.close = AsyncMock(side_effect=crash_in_the_window)
    assert await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com") is True
    assert claimed == [True]  # the listener left it to this recovery rather than scheduling another
    assert s.page is second and s.pages == [second]
    first.close.assert_awaited_once()  # replaced in its own slot by the next attempt
    assert [e.outcome for e in events] == ["recovered"]
    assert crash_recovery.claim_replacement_crash(second) is False


async def test_a_goto_that_redirected_elsewhere_is_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a failed navigation was judged, so a 302 to /login was reported as at its last URL."""
    from octowright.browser_pool import session_event_bus as _bus

    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    s.context.new_page.return_value.url = "https://example.com/login?next=%2F"
    assert await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com/") is True
    (event,) = events
    assert event.outcome == "recovered" and event.recovered_elsewhere is True and event.navigation_error is None
    (inc,) = incidents.recent(category="renderer_crash")
    assert inc["recovered_elsewhere"] is True and inc["navigation_error"] is None


async def test_a_refused_later_hop_on_the_redirect_document_is_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard's redirect document sits AT the last URL: the page's URL alone said it recovered there."""
    from octowright import ssrf_guard
    from octowright.browser_pool import session_event_bus as _bus

    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=False)
    fresh.url = "https://example.com/"  # still on the stub served for the first hop

    async def refused_later_hop(*_args: Any, **_kwargs: Any) -> None:
        ssrf_guard.frame_chain(fresh.main_frame).end("redirect to 'http://10.0.0.1/' refused", failed=False)

    fresh.goto = refused_later_hop
    assert await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com") is True
    (event,) = events
    assert event.recovered_elsewhere is True and "10.0.0.1" in event.navigation_error


async def test_a_page_left_on_the_redirect_document_is_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import ssrf_guard
    from octowright.browser_pool import session_event_bus as _bus

    events: list[Any] = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    s = _session()
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=False)
    fresh.goto = AsyncMock(side_effect=TimeoutError("Page.goto: Timeout 15000ms exceeded."))
    monkeypatch.setattr(ssrf_guard, "served_client_redirect_last", lambda frame: frame is fresh.main_frame)
    assert await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com") is True
    (event,) = events
    assert event.recovered_elsewhere is True


async def test_the_crash_probe_timing_out_does_not_report_the_session_unresponsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe's SessionCallTimeoutError escaped the crash_recovery lease and fired the gate's on_call_timeout hook."""
    hook_calls: list[str] = []
    s = _session()
    s._test_operation_gate = SessionOperationGate(
        s.instance_id,
        s.kind,
        queue_timeout_seconds=30,
        on_call_timeout=lambda operation, _exc: hook_calls.append(operation),
    )
    monkeypatch.setattr(crash_recovery, "_CRASH_SETTLE_SECONDS", 0.05)
    fresh = s.context.new_page.return_value
    fresh.is_closed = MagicMock(return_value=False)
    fresh.goto = AsyncMock(side_effect=RuntimeError("Page.goto: net::ERR_ABORTED"))

    async def wedged(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    fresh.evaluate = wedged
    assert await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com") is True
    assert hook_calls == []


def test_same_url_uses_the_shared_origin_normaliser() -> None:
    assert crash_recovery._same_url("https://example.com./", "https://EXAMPLE.com:443") is True
    assert crash_recovery._same_url("https://example.com/a?b=1#c", "https://example.com/a?b=1") is True
    assert crash_recovery._same_url("https://example.com/a", "http://example.com/a") is False
    assert crash_recovery._same_url("about:blank", "about:blank#x") is True
    assert crash_recovery._same_url(None, "https://example.com") is False


async def test_a_recovered_page_that_crashes_later_is_recovered_again() -> None:
    """Once swapped in, the replacement is the session's page: its next crash is a crash like any other."""
    s = _session()
    fresh = s.context.new_page.return_value
    assert await crash_recovery._recover(s, s.page, reload_timeout_ms=15000.0, url="https://example.com") is True
    assert crash_recovery.claim_replacement_crash(fresh) is False
    assert crash_recovery.claim_replacement_crash(None) is False


async def test_a_replacement_closed_by_its_own_crash_is_replaced_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Firefox reports a crashed replacement closed by the time goto raises: it failed the recovery outright."""
    import time

    s = _session()
    s._last_crash_monotonic = time.monotonic()
    dead = s.page
    crashed = _crashing_page(s, "crashed")
    crashed.is_closed = MagicMock(return_value=True)
    good = s.context.new_page.return_value
    s.context.new_page = AsyncMock(side_effect=[crashed, good])
    assert await crash_recovery._recover(s, dead, reload_timeout_ms=15000.0, url="https://example.com") is True
    assert s.page is good and s.pages == [good] and s._crash_recoveries == 2
