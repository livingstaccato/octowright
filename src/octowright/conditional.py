# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branching action types for macros: if_selector, try, try_each.

Recorded sessions are linear, but durable macros need to cope with sites that
re-roll their CSS classes (Discord, Slack, etc.). These three action types let
a macro author guard against that:

* ``if_selector`` — predicate on selector presence; runs ``then`` or ``else``.
* ``try`` — best-effort: run a sub-sequence and SUPPRESS errors. Useful for
  optional steps like dismissing a one-off cookie banner.
* ``try_each`` — run branches in order, succeed on first that completes; raise
  if all fail. The "v1 OR v2 OR v3 of this flow" hammer.

The handlers here are pure logic — they take an external `dispatch` callable
that knows how to run any single action (so simple actions and other
conditionals can nest freely).

JSON shapes:

    {"action": "if_selector", "selector": ".v1-modal", "present": true,
     "timeout_ms": 1000,
     "then": [{"action": "click", "selector": ".v1-close"}],
     "else": [{"action": "click", "selector": ".v2-dismiss-button"}]}

    {"action": "try", "actions": [
        {"action": "click", "selector": "#optional-cookie-accept"}
     ]}

    {"action": "try_each", "branches": [
        [{"action": "click", "selector": ".v1-close"}],
        [{"action": "click", "selector": ".v2-dismiss"}],
        [{"action": "press_key", "key": "Escape"}]
     ]}
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

if TYPE_CHECKING:
    from octowright.session import BrowserSession

log = get_logger(__name__)

# Type alias for the recursive dispatch callable: `(session, action) -> (executed, skipped)`.
DispatchFn = Callable[["BrowserSession", dict[str, Any]], Awaitable[tuple[int, int]]]

# Default predicate timeout — short, since we're polling, not blocking on a real wait.
_DEFAULT_PREDICATE_TIMEOUT_MS = 1000


async def selector_present(page: Any, selector: str, timeout_ms: int) -> bool:
    """Return True if at least one element matches `selector` within `timeout_ms`.

    Uses Playwright's `wait_for(state='attached')` so we report 'present' as
    soon as the element is in the DOM, even if it isn't visible yet. On
    timeout, returns False — does NOT raise.
    """
    try:
        await page.locator(selector).first.wait_for(state="attached", timeout=timeout_ms)
        return True
    except Exception:
        return False


async def do_if_selector(
    session: BrowserSession,
    action: dict[str, Any],
    dispatch: DispatchFn,
) -> tuple[int, int]:
    """Run `then` if the selector matches the expected presence; else run `else`.

    `present` defaults to True (i.e. "if the selector exists, run then").
    Either branch may be omitted — a missing branch is a no-op.
    """
    selector = action["selector"]
    expected_present = bool(action.get("present", True))
    timeout_ms = int(action.get("timeout_ms", _DEFAULT_PREDICATE_TIMEOUT_MS))
    actually_present = await selector_present(session.page, selector, timeout_ms)
    matched = actually_present == expected_present
    branch = action.get("then" if matched else "else") or []
    session.recorder.record(
        "if_selector",
        selector=selector,
        expected_present=expected_present,
        actually_present=actually_present,
        branch="then" if matched else "else",
        branch_size=len(branch),
    )
    e_total, s_total = 1, 0  # the if_selector itself counts as one executed step
    for sub in branch:
        e, s = await dispatch(session, sub)
        e_total += e
        s_total += s
    return e_total, s_total


async def do_try(
    session: BrowserSession,
    action: dict[str, Any],
    dispatch: DispatchFn,
) -> tuple[int, int]:
    """Run `actions` in order; SUPPRESS the first exception (and skip remaining).

    Useful for optional cleanup steps like dismissing a cookie banner that may
    or may not be present. Returns counts including everything attempted; the
    failed action is counted as `skipped` (because it didn't complete).
    """
    actions = action.get("actions", [])
    e_total, s_total = 1, 0  # the try wrapper counts as one executed
    for sub in actions:
        try:
            e, s = await dispatch(session, sub)
            e_total += e
            s_total += s
        except Exception as exc:
            session.recorder.record(
                "try_suppressed",
                failed_action=sub,
                error=repr(exc),
            )
            log.info("octowright.macro.try.suppressed", action=sub.get("action"), error=repr(exc))
            return e_total, s_total + 1
    return e_total, s_total


async def do_try_each(
    session: BrowserSession,
    action: dict[str, Any],
    dispatch: DispatchFn,
) -> tuple[int, int]:
    """Run branches in order; succeed on first whose every action completes.

    Raises RuntimeError if all branches fail. Useful when the same logical
    operation has multiple possible DOM forms (e.g. Discord v1 vs v2
    selectors).
    """
    branches = action.get("branches", [])
    if not branches:
        raise ValueError("try_each: at least one branch is required")

    last_error: Exception | None = None
    for branch_idx, branch in enumerate(branches):
        try:
            e_total, s_total = 1, 0  # the try_each wrapper counts as one executed
            for sub in branch:
                e, s = await dispatch(session, sub)
                e_total += e
                s_total += s
            session.recorder.record("try_each_succeeded", branch_idx=branch_idx, branch_size=len(branch))
            return e_total, s_total
        except Exception as exc:
            last_error = exc
            session.recorder.record(
                "try_each_branch_failed",
                branch_idx=branch_idx,
                error=repr(exc),
            )
            log.info("octowright.macro.try_each.branch_failed", branch_idx=branch_idx, error=repr(exc))

    raise RuntimeError(f"try_each: all {len(branches)} branches failed; last error: {last_error!r}") from last_error


CONDITIONAL_ACTIONS = frozenset({"if_selector", "try", "try_each"})


async def dispatch_conditional(
    session: BrowserSession,
    action: dict[str, Any],
    dispatch: DispatchFn,
) -> tuple[int, int]:
    """Entry point: dispatch any conditional action by name.

    Caller is expected to have already checked `action["action"] in CONDITIONAL_ACTIONS`.
    """
    kind = action["action"]
    if kind == "if_selector":
        return await do_if_selector(session, action, dispatch)
    if kind == "try":
        return await do_try(session, action, dispatch)
    if kind == "try_each":
        return await do_try_each(session, action, dispatch)
    raise ValueError(f"not a conditional action: {kind!r}")
