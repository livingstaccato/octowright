# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What ``expect_network_clean`` counts, over which window, and when it looks.

Counting: running counters, so a failure the bounded request deque evicted
still counts -- the deque is a diagnostic, not the ledger.

Window: the current macro run by default; ``since="mark"`` spans runs, from the
last ``mark_network_clean`` step, so a separate verify macro can judge the
journey before it without inheriting unrelated session history.

Timing: requests still in flight are waited for (bounded), because a click
returns before the POST it started has failed.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution
from octowright.macros.lint import lint_macro
from octowright.macros.runtime import _ACTION_MAP
from octowright.session.core import BrowserSession


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.is_closed = MagicMock(return_value=False)
    return BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


def _request(resource_type: str = "fetch", failure: str | None = None) -> MagicMock:
    request = MagicMock(url="https://api.test/x?token=abc", method="GET", resource_type=resource_type, failure=failure)
    request.headers = {}
    return request


def _fail(session: BrowserSession, failure: str = "net::ERR_CONNECTION_REFUSED") -> None:
    session._handle_request_failed(_request(failure=failure))


def _ok(session: BrowserSession) -> None:
    response = MagicMock(status=200, status_text="OK")
    response.request = _request()
    session._handle_response(response)


# --- counting survives the diagnostic deque ------------------------------------------


@pytest.mark.anyio
async def test_evicted_failures_still_count(session: BrowserSession) -> None:
    session._network_requests = deque(maxlen=3)
    session.mark_network_clean_window()
    for _ in range(5):
        _fail(session)
    with pytest.raises(RuntimeError, match=r"5 failed request\(s\)"):
        await session.expect_network_clean(settle_timeout_ms=0)


@pytest.mark.anyio
async def test_a_failure_pushed_out_by_successes_still_counts(session: BrowserSession) -> None:
    session._network_requests = deque(maxlen=5)
    session.mark_network_clean_window()
    _fail(session)
    for _ in range(5):
        _ok(session)
    with pytest.raises(RuntimeError, match=r"1 failed request\(s\)"):
        await session.expect_network_clean(settle_timeout_ms=0)


@pytest.mark.anyio
async def test_a_zero_length_deque_hides_nothing(session: BrowserSession) -> None:
    """OCTOWRIGHT_NETWORK_EVENT_LIMIT=0 turns off the diagnostic, not the assertion."""
    session._network_requests = deque(maxlen=0)
    _fail(session)
    with pytest.raises(RuntimeError, match=r"1 failed request\(s\)"):
        await session.expect_network_clean(settle_timeout_ms=0)


# --- the cross-run window --------------------------------------------------------------


@pytest.mark.anyio
async def test_since_mark_spans_a_later_run_reset(session: BrowserSession) -> None:
    await session.mark_network_clean()
    _fail(session)  # the journey
    session.mark_network_clean_window()  # the verify macro's own run starts
    assert (await session.expect_network_clean(settle_timeout_ms=0))["failed_requests"] == 0
    with pytest.raises(RuntimeError, match=r"1 failed request\(s\)"):
        await session.expect_network_clean(since="mark", settle_timeout_ms=0)


@pytest.mark.anyio
async def test_since_mark_ignores_history_before_the_mark(session: BrowserSession) -> None:
    _fail(session)  # unrelated exploration, earlier in the session
    await session.mark_network_clean()
    assert (await session.expect_network_clean(since="mark", settle_timeout_ms=0))["failed_requests"] == 0


@pytest.mark.anyio
async def test_since_mark_without_a_mark_says_so(session: BrowserSession) -> None:
    with pytest.raises(RuntimeError, match="mark_network_clean"):
        await session.expect_network_clean(since="mark", settle_timeout_ms=0)


@pytest.mark.anyio
async def test_an_unknown_window_is_refused(session: BrowserSession) -> None:
    with pytest.raises(ValueError, match="since"):
        await session.expect_network_clean(since="session", settle_timeout_ms=0)


@pytest.mark.anyio
async def test_the_mark_step_is_recorded(session: BrowserSession) -> None:
    await session.mark_network_clean()
    session.recorder.record.assert_called_with("mark_network_clean")


def test_the_mark_step_replays_and_lints() -> None:
    assert _ACTION_MAP["mark_network_clean"] == "mark_network_clean"
    actions = [{"action": "mark_network_clean"}, {"action": "expect_network_clean", "since": "mark"}]
    assert [i.message for i in lint_macro({"name": "m", "actions": actions}) if i.severity == "error"] == []


# --- in-flight requests are waited for ---------------------------------------------------


@pytest.mark.anyio
async def test_a_request_that_fails_while_the_check_waits_is_counted(session: BrowserSession) -> None:
    pending = _request()
    session._handle_request_started(pending, session.page)

    async def fail_later() -> None:
        await asyncio.sleep(0.15)
        pending.failure = "net::ERR_CONNECTION_REFUSED"
        session._handle_request_failed(pending)

    task = asyncio.ensure_future(fail_later())
    with pytest.raises(RuntimeError, match=r"1 failed request\(s\)"):
        await session.expect_network_clean(settle_timeout_ms=2000)
    await task


@pytest.mark.anyio
async def test_a_request_that_never_finishes_is_reported_not_failed(session: BrowserSession) -> None:
    session._handle_request_started(_request(), session.page)
    started = time.monotonic()
    result = await session.expect_network_clean(settle_timeout_ms=150)
    assert time.monotonic() - started < 1.5
    assert result["in_flight"] == 1 and result["failed_requests"] == 0


@pytest.mark.anyio
async def test_settle_zero_judges_immediately(session: BrowserSession) -> None:
    session._handle_request_started(_request(), session.page)
    started = time.monotonic()
    await session.expect_network_clean(settle_timeout_ms=0)
    assert time.monotonic() - started < 0.1


@pytest.mark.anyio
@pytest.mark.parametrize("resource_type", ["eventsource", "websocket", "media"])
async def test_long_lived_streams_are_not_waited_for(session: BrowserSession, resource_type: str) -> None:
    session._handle_request_started(_request(resource_type), session.page)
    started = time.monotonic()
    result = await session.expect_network_clean(settle_timeout_ms=3000)
    assert time.monotonic() - started < 1.0 and result["in_flight"] == 0


@pytest.mark.anyio
async def test_a_finished_request_stops_the_wait(session: BrowserSession) -> None:
    request = _request()
    session._handle_request_started(request, session.page)
    session._handle_request_finished(request)
    started = time.monotonic()
    await session.expect_network_clean(settle_timeout_ms=3000)
    assert time.monotonic() - started < 1.0


@pytest.mark.anyio
async def test_a_closed_pages_requests_are_not_waited_for(session: BrowserSession) -> None:
    closed = MagicMock()
    session._handle_request_started(_request(), closed)
    session._handle_request_started(_request(), session.page)
    session._forget_page_requests(closed)
    assert session.pending_requests() == 1
    session._forget_page_requests(session.page)
    started = time.monotonic()
    await session.expect_network_clean(settle_timeout_ms=3000)
    assert time.monotonic() - started < 1.0


def test_in_flight_tracking_is_bounded(session: BrowserSession) -> None:
    for _ in range(5000):
        session._handle_request_started(_request(), session.page)
    assert len(session._inflight_requests) <= 1000


def test_listeners_track_request_lifecycle(session: BrowserSession) -> None:
    from octowright.browser_pool.listeners import _wire_listeners

    page = MagicMock()
    _wire_listeners(session, page)
    events = {call.args[0] for call in page.on.call_args_list}
    assert {"request", "requestfinished", "requestfailed", "pageerror", "close"} <= events


# --- run_macro resets the per-run window (no browser needed) ----------------------------


def _load(monkeypatch: pytest.MonkeyPatch, macros: dict[str, list[dict[str, Any]]]) -> None:
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})


@pytest.mark.anyio
async def test_run_macro_judges_only_its_own_run(session: BrowserSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _fail(session)  # before the run
    _load(monkeypatch, {"verify": [{"action": "expect_network_clean", "settle_timeout_ms": 0}]})
    result = await execution.run_macro(session, "verify")
    assert result["executed"] == 1


@pytest.mark.anyio
async def test_a_verify_macro_judges_the_journey_since_its_mark(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load(
        monkeypatch,
        {
            "journey": [{"action": "mark_network_clean"}],
            "verify": [{"action": "expect_network_clean", "since": "mark", "settle_timeout_ms": 0}],
        },
    )
    await execution.run_macro(session, "journey")
    _fail(session)  # the journey's traffic, after its mark
    with pytest.raises(RuntimeError, match=r"1 failed request\(s\)"):
        await execution.run_macro(session, "verify")
