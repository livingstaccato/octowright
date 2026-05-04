# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for octowright.conditional — if_selector / try / try_each.

Uses stub session/page objects (no real Playwright). The dispatch callable is
also stubbed so we can observe the recursion explicitly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from octowright import conditional as _cond

# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------


class _StubLocator:
    def __init__(self, *, attached: bool, raise_kind: type[BaseException] | None = None) -> None:
        self._attached = attached
        self._raise_kind = raise_kind

    @property
    def first(self) -> _StubLocator:
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        if self._raise_kind is not None:
            raise self._raise_kind("forced")
        if not self._attached:
            raise TimeoutError(f"selector not attached within {timeout}ms")


class _StubPage:
    """Page stub that returns a configurable locator for any selector."""

    def __init__(self, *, attached_selectors: set[str] | None = None) -> None:
        self._attached = attached_selectors or set()

    def locator(self, selector: str) -> _StubLocator:
        return _StubLocator(attached=selector in self._attached)


class _StubSession:
    def __init__(self, page: _StubPage) -> None:
        self.page = page
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.recorder = self  # self-as-recorder; .record is below.

    def record(self, action: str, **fields: Any) -> None:
        self.records.append((action, fields))


def _capturing_dispatch() -> tuple[
    Callable[[_StubSession, dict[str, Any]], Awaitable[tuple[int, int]]],
    list[dict[str, Any]],
]:
    """Returns (dispatch_callable, captured_actions_list).

    Default behaviour: every dispatched action returns (1, 0). Tests that need
    failures monkey-patch the captured list's behaviour by raising in a custom
    dispatch — see _dispatch_factory_failing.
    """
    captured: list[dict[str, Any]] = []

    async def dispatch(session: _StubSession, action: dict[str, Any]) -> tuple[int, int]:
        captured.append(action)
        return 1, 0

    return dispatch, captured


def _dispatch_factory_failing(failing_action_kinds: set[str]) -> Callable[..., Awaitable[tuple[int, int]]]:
    """Dispatch that raises RuntimeError when an action's `action` field is in the set."""

    async def dispatch(session: _StubSession, action: dict[str, Any]) -> tuple[int, int]:
        if action.get("action") in failing_action_kinds:
            raise RuntimeError(f"forced failure on {action['action']!r}")
        return 1, 0

    return dispatch


# ---------------------------------------------------------------------------
# selector_present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selector_present_true_when_attached() -> None:
    page = _StubPage(attached_selectors={".modal"})
    assert await _cond.selector_present(page, ".modal", timeout_ms=500) is True


@pytest.mark.asyncio
async def test_selector_present_false_on_timeout() -> None:
    page = _StubPage(attached_selectors=set())
    assert await _cond.selector_present(page, ".missing", timeout_ms=10) is False


@pytest.mark.asyncio
async def test_selector_present_false_on_any_exception() -> None:
    """Wait throwing a non-Timeout shouldn't propagate — predicate must be infallible."""

    class _BoomPage:
        def locator(self, selector: str) -> Any:
            return _StubLocator(attached=False, raise_kind=ValueError)

    assert await _cond.selector_present(_BoomPage(), ".x", timeout_ms=10) is False


# ---------------------------------------------------------------------------
# do_if_selector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_if_selector_runs_then_when_present_matches() -> None:
    page = _StubPage(attached_selectors={".modal"})
    session = _StubSession(page)
    dispatch, captured = _capturing_dispatch()
    action = {
        "action": "if_selector",
        "selector": ".modal",
        "present": True,
        "then": [{"action": "click", "selector": ".close"}],
        "else": [{"action": "press_key", "key": "Escape"}],
    }
    executed, skipped = await _cond.do_if_selector(session, action, dispatch)
    assert (executed, skipped) == (2, 0)  # 1 for if_selector + 1 for click
    assert captured == [{"action": "click", "selector": ".close"}]


@pytest.mark.asyncio
async def test_if_selector_runs_else_when_present_mismatches() -> None:
    page = _StubPage(attached_selectors=set())
    session = _StubSession(page)
    dispatch, captured = _capturing_dispatch()
    action = {
        "action": "if_selector",
        "selector": ".modal",
        "present": True,
        "then": [{"action": "click", "selector": ".close"}],
        "else": [{"action": "press_key", "key": "Escape"}],
    }
    executed, _ = await _cond.do_if_selector(session, action, dispatch)
    assert executed == 2
    assert captured == [{"action": "press_key", "key": "Escape"}]


@pytest.mark.asyncio
async def test_if_selector_present_false_inverts_check() -> None:
    """`present: false` means 'run then if the selector is ABSENT'."""
    page = _StubPage(attached_selectors=set())
    session = _StubSession(page)
    dispatch, captured = _capturing_dispatch()
    action = {
        "action": "if_selector",
        "selector": ".error-banner",
        "present": False,
        "then": [{"action": "click", "selector": ".submit"}],
    }
    executed, _ = await _cond.do_if_selector(session, action, dispatch)
    assert executed == 2
    assert captured == [{"action": "click", "selector": ".submit"}]


@pytest.mark.asyncio
async def test_if_selector_missing_branch_is_noop() -> None:
    """Omitting the matched branch entirely — counts only the if_selector itself."""
    page = _StubPage(attached_selectors={".modal"})
    session = _StubSession(page)
    dispatch, captured = _capturing_dispatch()
    action = {
        "action": "if_selector",
        "selector": ".modal",
        "present": True,
        "else": [{"action": "click", "selector": ".x"}],  # no `then`
    }
    executed, skipped = await _cond.do_if_selector(session, action, dispatch)
    assert (executed, skipped) == (1, 0)
    assert captured == []


@pytest.mark.asyncio
async def test_if_selector_records_predicate_outcome() -> None:
    """The recorder gets the full predicate metadata so debugging is possible from JSONL."""
    page = _StubPage(attached_selectors={".modal"})
    session = _StubSession(page)
    dispatch, _ = _capturing_dispatch()
    await _cond.do_if_selector(
        session,
        {"action": "if_selector", "selector": ".modal", "present": True, "then": []},
        dispatch,
    )
    assert len(session.records) == 1
    name, fields = session.records[0]
    assert name == "if_selector"
    assert fields["selector"] == ".modal"
    assert fields["actually_present"] is True
    assert fields["branch"] == "then"


# ---------------------------------------------------------------------------
# do_try
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_runs_all_actions_on_success() -> None:
    session = _StubSession(_StubPage())
    dispatch, captured = _capturing_dispatch()
    action = {
        "action": "try",
        "actions": [
            {"action": "click", "selector": "#a"},
            {"action": "click", "selector": "#b"},
        ],
    }
    executed, skipped = await _cond.do_try(session, action, dispatch)
    assert (executed, skipped) == (3, 0)  # try wrapper + 2 clicks
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_try_suppresses_first_failure_and_stops() -> None:
    """The action that raises is counted as skipped; later actions are NOT attempted."""
    session = _StubSession(_StubPage())
    dispatch = _dispatch_factory_failing({"flaky"})
    action = {
        "action": "try",
        "actions": [
            {"action": "click", "selector": "#first"},
            {"action": "flaky", "selector": "#bad"},
            {"action": "click", "selector": "#never"},
        ],
    }
    executed, skipped = await _cond.do_try(session, action, dispatch)
    # 1 (try wrapper) + 1 (first click) executed; 1 skipped (the flaky failure).
    assert executed == 2
    assert skipped == 1
    # Recorder captures the suppression for postmortem.
    assert any(name == "try_suppressed" for name, _ in session.records)


# ---------------------------------------------------------------------------
# do_try_each
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_each_succeeds_on_first_working_branch() -> None:
    session = _StubSession(_StubPage())
    dispatch = _dispatch_factory_failing({"v1-only"})
    action = {
        "action": "try_each",
        "branches": [
            [{"action": "v1-only", "selector": ".v1"}],  # fails
            [{"action": "click", "selector": ".v2"}],  # succeeds
            [{"action": "click", "selector": ".v3"}],  # not reached
        ],
    }
    executed, skipped = await _cond.do_try_each(session, action, dispatch)
    # try_each (1) + click in branch 2 (1).
    assert executed == 2
    assert skipped == 0
    # Recorder has both the failed branch and the success.
    record_names = [name for name, _ in session.records]
    assert "try_each_branch_failed" in record_names
    assert "try_each_succeeded" in record_names


@pytest.mark.asyncio
async def test_try_each_raises_when_all_branches_fail() -> None:
    session = _StubSession(_StubPage())
    dispatch = _dispatch_factory_failing({"a", "b"})
    action = {
        "action": "try_each",
        "branches": [
            [{"action": "a"}],
            [{"action": "b"}],
        ],
    }
    with pytest.raises(RuntimeError, match="all 2 branches failed"):
        await _cond.do_try_each(session, action, dispatch)


@pytest.mark.asyncio
async def test_try_each_empty_branches_raises_value_error() -> None:
    session = _StubSession(_StubPage())
    dispatch, _ = _capturing_dispatch()
    with pytest.raises(ValueError, match="at least one branch"):
        await _cond.do_try_each(session, {"action": "try_each", "branches": []}, dispatch)


# ---------------------------------------------------------------------------
# dispatch_conditional router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_conditional_routes_to_each_handler() -> None:
    session = _StubSession(_StubPage())
    dispatch, _ = _capturing_dispatch()

    e, _ = await _cond.dispatch_conditional(
        session,
        {"action": "if_selector", "selector": ".x", "present": False, "then": []},
        dispatch,
    )
    assert e >= 1

    e, _ = await _cond.dispatch_conditional(
        session,
        {"action": "try", "actions": []},
        dispatch,
    )
    assert e >= 1

    e, _ = await _cond.dispatch_conditional(
        session,
        {"action": "try_each", "branches": [[]]},
        dispatch,
    )
    assert e >= 1


@pytest.mark.asyncio
async def test_dispatch_conditional_rejects_unknown_kind() -> None:
    session = _StubSession(_StubPage())
    dispatch, _ = _capturing_dispatch()
    with pytest.raises(ValueError, match="not a conditional action"):
        await _cond.dispatch_conditional(session, {"action": "click"}, dispatch)


# ---------------------------------------------------------------------------
# nesting (recursive dispatch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_if_selector_can_nest_inside_try_each() -> None:
    """Realistic Discord-style flow: try v1-modal-close, fall back to v2-or-noop."""
    page = _StubPage(attached_selectors={".v2-dismiss"})
    session = _StubSession(page)

    # Real recursive dispatch: only handles conditionals, falls through to a
    # capturing simple-stub for everything else.
    captured: list[dict[str, Any]] = []

    async def dispatch(session: _StubSession, action: dict[str, Any]) -> tuple[int, int]:
        if action.get("action") in _cond.CONDITIONAL_ACTIONS:
            return await _cond.dispatch_conditional(session, action, dispatch)
        if action.get("action") == "fail":
            raise RuntimeError("simulated failure")
        captured.append(action)
        return 1, 0

    nested = {
        "action": "try_each",
        "branches": [
            [{"action": "fail"}],  # branch 1: fails
            [
                {  # branch 2: if selector present, click it; else no-op
                    "action": "if_selector",
                    "selector": ".v2-dismiss",
                    "present": True,
                    "then": [{"action": "click", "selector": ".v2-dismiss"}],
                }
            ],
        ],
    }
    executed, _ = await _cond.dispatch_conditional(session, nested, dispatch)
    # try_each(1) + if_selector(1) + click(1) = 3
    assert executed == 3
    assert captured == [{"action": "click", "selector": ".v2-dismiss"}]
