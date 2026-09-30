# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_run`` reports what its ``expect_network_clean`` / ``expect_no_text`` steps saw.

A network check that judged with a request still pending, and a text check
whose selector matched nothing, both pass by design -- and before this read
exactly like a clean pass: the session method returned the counts and macro
dispatch dropped them. Pass/fail is unchanged; the run's result now carries
an ``assertions`` list with a ``warning`` on each such pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution
from octowright.session.core import BrowserSession

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.on = MagicMock()
    page.frames = [page]
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


def _load(monkeypatch: pytest.MonkeyPatch, actions: list[dict[str, Any]], name: str = "m") -> None:
    macros = {name: actions}
    monkeypatch.setattr(execution, "load_macro", lambda n: {"name": n, "actions": macros[n]})


def _request() -> MagicMock:
    return MagicMock(url="https://api.test/slow", method="POST", resource_type="fetch", failure=None)


def _starts_a_request_that_never_ends(session: BrowserSession) -> None:
    """The step before the check leaves a request running, as a click on Submit does."""

    async def evaluate(expression: str, *_args: Any) -> None:
        if expression == "submit()":  # not the status pill's own evaluates
            session._network.request_started(_request(), session.page)

    session.page.evaluate = AsyncMock(side_effect=evaluate)


def _scan_matches(session: BrowserSession, matched: int) -> None:
    session.page.evaluate = AsyncMock(return_value={"pieces": [], "overlay": "", "matched": matched})
    session._snapshot_leaks = AsyncMock(return_value=False)  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_a_request_still_pending_at_the_deadline_is_reported(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _starts_a_request_that_never_ends(session)
    _load(
        monkeypatch,
        [{"action": "evaluate", "expression": "submit()"}, {"action": "expect_network_clean", "settle_timeout_ms": 0}],
    )
    result = await execution.run_macro(session, "m")
    assert result["executed"] == 2  # still a pass: semantics are unchanged
    assert result["assertions"] == [
        {
            "step": 1,
            "action": "expect_network_clean",
            "failed_requests": 0,
            "page_errors": 0,
            "in_flight": 1,
            "warning": "1 request(s) still in flight when the settle wait ended were not judged",
        }
    ]


@pytest.mark.anyio
async def test_requests_dropped_from_tracking_are_reported(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def flood(expression: str, *_args: Any) -> None:
        if expression == "flood()":
            for _ in range(1005):
                session._network.request_started(_request(), session.page)

    session.page.evaluate = AsyncMock(side_effect=flood)
    _load(
        monkeypatch,
        [{"action": "evaluate", "expression": "flood()"}, {"action": "expect_network_clean", "settle_timeout_ms": 0}],
    )
    (observation,) = (await execution.run_macro(session, "m"))["assertions"]
    assert observation["in_flight"] == 1000
    assert observation["in_flight_untracked"] == 5
    assert "5 request(s) were dropped from in-flight tracking" in observation["warning"]


@pytest.mark.anyio
async def test_a_clean_network_check_carries_no_warning(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load(monkeypatch, [{"action": "expect_network_clean", "settle_timeout_ms": 0}])
    result = await execution.run_macro(session, "m")
    assert result["assertions"] == [
        {"step": 0, "action": "expect_network_clean", "failed_requests": 0, "page_errors": 0, "in_flight": 0}
    ]


@pytest.mark.anyio
async def test_a_selector_that_matched_nothing_is_reported(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_matches(session, 0)
    _load(monkeypatch, [{"action": "expect_no_text", "text": "{{secret}}", "selector": "#error-banner"}])
    result = await execution.run_macro(session, "m", {"secret": SECRET})
    (observation,) = result["assertions"]
    assert observation["matched"] == 0
    assert observation["selector"] == "#error-banner"
    assert observation["warning"] == "selector '#error-banner' matched no element, so no text was checked"
    assert SECRET not in repr(result)


@pytest.mark.anyio
async def test_a_whole_page_check_that_matched_nothing_is_not_a_warning(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``body`` always exists; zero there is an empty page, not a stale selector."""
    _scan_matches(session, 0)
    _load(monkeypatch, [{"action": "expect_no_text", "text": SECRET}])
    (observation,) = (await execution.run_macro(session, "m"))["assertions"]
    assert "warning" not in observation and observation["selector"] == "body"


@pytest.mark.anyio
async def test_a_nested_check_is_reported_at_its_top_level_step(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    macros = {
        "outer": [{"action": "evaluate", "expression": "x()"}, {"action": "macro_call", "name": "inner"}],
        "inner": [{"action": "expect_network_clean", "settle_timeout_ms": 0}],
    }
    monkeypatch.setattr(execution, "load_macro", lambda n: {"name": n, "actions": macros[n]})
    (observation,) = (await execution.run_macro(session, "outer"))["assertions"]
    assert observation["step"] == 1


@pytest.mark.anyio
async def test_a_run_without_either_check_has_no_assertions_key(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load(monkeypatch, [{"action": "evaluate", "expression": "x()"}])
    assert "assertions" not in await execution.run_macro(session, "m")


@pytest.mark.anyio
async def test_a_failed_run_reports_the_checks_that_passed_before_it(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pending request that passed step 1 may be the explanation for step 2."""
    _starts_a_request_that_never_ends(session)
    _load(
        monkeypatch,
        [
            {"action": "evaluate", "expression": "submit()"},
            {"action": "expect_network_clean", "settle_timeout_ms": 0},
            {"action": "expect_js", "expression": "false"},
        ],
    )
    with pytest.raises(RuntimeError) as excinfo:
        await execution.run_macro(session, "m")
    payload = excinfo.value.args[0]
    assert payload["failed_at_step"] == 2
    assert [o["in_flight"] for o in payload["assertions"]] == [1]


@pytest.mark.anyio
async def test_a_secret_substituted_into_the_selector_is_scrubbed(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_matches(session, 0)
    _load(monkeypatch, [{"action": "expect_no_text", "text": "{{secret}}", "selector": "[data-x='{{secret}}']"}])
    result = await execution.run_macro(session, "m", {"secret": SECRET})
    assert result["assertions"][0]["matched"] == 0
    assert SECRET not in repr(result["assertions"])
