# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Opt-in strictness for the two checks that can pass on less than they were asked.

By default a request still in flight at the settle deadline, or a selector that
matched nothing, is a pass with a ``warning``. A step that sets
``require_settled`` (``expect_network_clean``) or ``require_match``
(``expect_no_text``) turns exactly that caveat into a failure. Replay and the
exported CLI word it with the same function.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.assertion_warnings import strict_option, strict_refusal
from octowright.browser_pool.listeners import _wire_listeners
from octowright.macros import execution
from octowright.macros.lint import lint_macro
from octowright.session.core import BrowserSession
from tests.macro_lint.test_cli_export_execution import _FakePage
from tests.macro_lint.test_cli_export_negative_asserts import _fire, _Req, _run

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


class _Page:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.main_frame = object()
        self.url = "https://octowright.com/"

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        self.handlers[event].remove(handler)

    def is_closed(self) -> bool:
        return False

    def fire(self, event: str, payload: Any) -> None:
        for handler in self.handlers.get(event, []):
            handler(payload)


def _session(tmp_path: Path, frame: Any = None) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.is_closed = MagicMock(return_value=False)
    page.on = MagicMock()
    page.frames = [frame] if frame is not None else []
    if frame is not None:  # a selector scans the target itself, not its frames
        page.evaluate = frame.evaluate
        page.is_detached = frame.is_detached
    return BrowserSession(
        instance_id="test",
        kind="firefox",
        label="t",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


def _pending(session: BrowserSession) -> None:
    page = _Page()
    session.pages.append(page)  # type: ignore[arg-type]
    _wire_listeners(session, page)
    session.enable_inflight_tracking()
    request = MagicMock(url="https://api.test/x", method="GET", resource_type="fetch", failure=None)
    request.headers = {}
    request.frame = page.main_frame
    request.is_navigation_request = MagicMock(return_value=False)
    page.fire("request", request)


def _frame(matched: int) -> MagicMock:
    frame = MagicMock()
    frame.evaluate = AsyncMock(return_value={"pieces": ["Welcome"], "matched": matched, "truncated": False})
    frame.is_detached = MagicMock(return_value=False)
    return frame


# --- the shared wording -------------------------------------------------------------


def test_off_never_refuses() -> None:
    assert strict_refusal("expect_network_clean", {"in_flight": 3}, required=False) is None
    assert strict_refusal("expect_no_text", {"matched": 0, "selector": "#x"}, required=False) is None


def test_a_settled_clean_pass_is_not_refused() -> None:
    assert strict_refusal("expect_network_clean", {"in_flight": 0}, required=True) is None
    assert strict_refusal("expect_no_text", {"matched": 2, "selector": "#x"}, required=True) is None


def test_the_refusal_names_the_option_and_the_caveat() -> None:
    message = strict_refusal("expect_network_clean", {"in_flight": 2}, required=True)
    assert message is not None and "require_settled" in message and "2 request(s) still in flight" in message
    untracked = strict_refusal("expect_network_clean", {"in_flight": 0, "in_flight_untracked": 1}, required=True)
    assert untracked is not None and "dropped from in-flight tracking" in untracked


def test_require_match_also_refuses_an_empty_body() -> None:
    """The default warning skips ``body`` (no body is not a stale selector); asked for a match, it is not one."""
    message = strict_refusal("expect_no_text", {"matched": 0, "selector": "body"}, required=True)
    assert message is not None and "require_match" in message


@pytest.mark.parametrize("bad", ["yes", 1, 0, None, "true"])
def test_a_non_boolean_option_is_refused(bad: Any) -> None:
    with pytest.raises(ValueError, match="require_settled must be true or false"):
        strict_option("expect_network_clean", bad)


# --- the session methods ------------------------------------------------------------


@pytest.mark.anyio
async def test_require_settled_fails_a_check_that_ended_with_a_request_in_flight(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _pending(session)
    with pytest.raises(RuntimeError, match="require_settled"):
        await session.expect_network_clean(settle_timeout_ms=0, require_settled=True)


@pytest.mark.anyio
async def test_without_it_the_same_check_passes(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _pending(session)
    assert (await session.expect_network_clean(settle_timeout_ms=0))["in_flight"] == 1


@pytest.mark.anyio
async def test_require_settled_is_recorded_so_replay_keeps_it(tmp_path: Path) -> None:
    session = _session(tmp_path)
    await session.expect_network_clean(settle_timeout_ms=0, require_settled=True)
    assert session.recorder.record.call_args.kwargs["require_settled"] is True
    await session.expect_network_clean(settle_timeout_ms=0)
    assert "require_settled" not in session.recorder.record.call_args.kwargs


@pytest.mark.anyio
async def test_require_settled_rejects_a_string(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="require_settled"):
        await _session(tmp_path).expect_network_clean(settle_timeout_ms=0, require_settled="yes")  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_require_match_fails_a_selector_that_matched_nothing(tmp_path: Path) -> None:
    session = _session(tmp_path, _frame(matched=0))
    with pytest.raises(RuntimeError, match="require_match") as excinfo:
        await session.expect_no_text(SECRET, selector="#error-banner", require_match=True)
    assert SECRET not in str(excinfo.value)


@pytest.mark.anyio
async def test_require_match_passes_when_the_selector_matched(tmp_path: Path) -> None:
    session = _session(tmp_path, _frame(matched=1))
    assert (await session.expect_no_text(SECRET, selector="#error-banner", require_match=True))["matched"] == 1
    assert session.recorder.record.call_args.kwargs["require_match"] is True


@pytest.mark.anyio
async def test_replay_passes_the_option_through(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _pending(session)
    step = {"action": "expect_network_clean", "settle_timeout_ms": 0, "require_settled": True}
    with pytest.raises(RuntimeError, match="require_settled"):
        await execution._dispatch_simple(session, step)


def test_both_options_lint() -> None:
    actions = [
        {"action": "expect_network_clean", "require_settled": True},
        {"action": "expect_no_text", "text": "{{password}}", "selector": "#x", "require_match": True},
    ]
    assert [i.message for i in lint_macro({"name": "m", "actions": actions}) if i.severity == "error"] == []


# --- the exported CLI ---------------------------------------------------------------


def test_the_export_fails_an_unsettled_check_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    def pending(page: _FakePage) -> None:
        _fire(page, "request", _Req(resource_type="fetch", failure=None))

    step = {"action": "expect_network_clean", "settle_timeout_ms": 0, "require_settled": True}
    with pytest.raises(BaseException, match="require_settled"):
        _run(monkeypatch, [step], on_page=pending)


def test_the_export_fails_an_unmatched_selector_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    def nothing_matches(page: _FakePage) -> None:
        page.matched = 0

    step = {"action": "expect_no_text", "text": SECRET, "selector": "#x", "require_match": True}
    with pytest.raises(BaseException, match="require_match") as excinfo:
        _run(monkeypatch, [step], on_page=nothing_matches)
    assert SECRET not in str(excinfo.value)


def test_the_export_refuses_a_non_boolean_option(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(BaseException, match="require_settled must be true or false"):
        _run(monkeypatch, [{"action": "expect_network_clean", "require_settled": "yes"}])
