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
  optional steps like dismissing a one-off cookie banner. A safety refusal
  (``safety_stop.SafetyStop``: a credential, SSRF-policy or classified-
  screenshot refusal) is never suppressed, here or by ``try_each``: it
  fails the run.

  What a suppressed step leaves behind -- the ``try_suppressed`` /
  ``try_each_branch_failed`` row and log line -- names the step as the macro
  WROTE it (`written_step`), never the expanded one carrying a substituted
  value, and its error by type alone once the session holds a classified value
  (`safe_error`): an error is free text, and a locator or value built from an
  argument rides in it verbatim, in whatever case the engine echoed it.
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

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright.safety_stop import SafetyStop

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)

# Type alias for the recursive dispatch callable: `(session, action) -> (executed, skipped)`.
DispatchFn = Callable[["SessionLike", dict[str, Any]], Awaitable[tuple[int, int]]]

# Default predicate timeout — short, since we're polling, not blocking on a real wait.
_DEFAULT_PREDICATE_TIMEOUT_MS = 1000

# Each expanded step's written twin, keyed by the expanded dict's id (the dict is
# held too, so the id cannot be reused while the run lives). None outside a run.
_WRITTEN_STEPS: ContextVar[dict[int, tuple[Any, Any]] | None] = ContextVar("octowright_written_steps", default=None)
_CONTAINER_KEYS = ("actions", "branches", "then", "else")


@contextmanager
def written_steps_scope() -> Iterator[None]:
    """Hold the written twins of a macro run's steps until the run ends; nested runs share it."""
    if _WRITTEN_STEPS.get() is not None:
        yield
        return
    token = _WRITTEN_STEPS.set({})
    try:
        yield
    finally:
        _WRITTEN_STEPS.reset(token)


def register_written_steps(written: Any, expanded: Any) -> None:
    """Pair each step of *expanded* with the same step in *written*, at every container depth.

    Expansion keeps the shape -- lists the same length, containers under the
    same keys -- so the two walk in step; a mismatch just leaves that step
    unpaired, and `written_step` falls back to its action name.
    """
    registry = _WRITTEN_STEPS.get()
    if registry is not None:
        _pair(written, expanded, registry)


def _pair(written: Any, expanded: Any, registry: dict[int, tuple[Any, Any]]) -> None:
    if isinstance(expanded, list) and isinstance(written, list) and len(expanded) == len(written):
        for written_item, expanded_item in zip(written, expanded, strict=True):
            _pair(written_item, expanded_item, registry)
    elif isinstance(expanded, dict) and isinstance(written, dict):
        registry[id(expanded)] = (expanded, written)
        for key in _CONTAINER_KEYS:
            _pair(written.get(key), expanded.get(key), registry)


def written_step(step: dict[str, Any]) -> Any:
    """*step* as the macro wrote it, or just its action name when the run did not pair it."""
    entry = (_WRITTEN_STEPS.get() or {}).get(id(step))
    if entry is not None and entry[0] is step:
        return entry[1]
    return {"action": step.get("action")}


def safe_error(session: SessionLike, exc: BaseException) -> str:
    """``repr(exc)``, or only its type once the session holds a classified value.

    Type alone because a scrub matches the spellings it was given, and an
    engine's error echoes a locator or value in its own (an upper-cased or
    escaped copy of the argument). Imported here, not at module level:
    ``octowright.macros`` imports this module.
    """
    from octowright.macros.privacy_ledger import with_session_values

    return type(exc).__name__ if with_session_values(session, ()) else repr(exc)


async def selector_present(session: SessionLike, selector: str, timeout_ms: int) -> bool:
    """Return True if at least one element matches `selector` within `timeout_ms`.

    Uses Playwright's `wait_for(state='attached')` so we report 'present' as
    soon as the element is in the DOM, even if it isn't visible yet. On
    timeout, returns False — does NOT raise.

    Acquires the ``macro_condition`` lease before dereferencing the active
    target (frame or page); a caller already holding a root lease (e.g. a
    macro's ``macro_run``) re-enters it in the same task without queueing.
    """
    async with session.operation("macro_condition"):
        try:
            await session._target().locator(selector).first.wait_for(state="attached", timeout=timeout_ms)
            return True
        except Exception:
            return False


async def do_if_selector(
    session: SessionLike,
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
    actually_present = await selector_present(session, selector, timeout_ms)
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
    session: SessionLike,
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
        except SafetyStop:
            # A safety check's verdict on the macro (a credential, SSRF or
            # screenshot refusal), not a step that missed: suppressing it would
            # report the run as a success.
            raise
        except Exception as exc:
            error = safe_error(session, exc)
            session.recorder.record("try_suppressed", failed_action=written_step(sub), error=error)
            log.info("octowright.macro.try.suppressed", action=sub.get("action"), error=error)
            return e_total, s_total + 1
    return e_total, s_total


async def do_try_each(
    session: SessionLike,
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
        except SafetyStop:
            # Not a branch that missed: never fall through to the next one.
            raise
        except Exception as exc:
            last_error = exc
            error = safe_error(session, exc)
            session.recorder.record("try_each_branch_failed", branch_idx=branch_idx, error=error)
            log.info("octowright.macro.try_each.branch_failed", branch_idx=branch_idx, error=error)

    shown = safe_error(session, last_error) if last_error is not None else None
    raise RuntimeError(f"try_each: all {len(branches)} branches failed; last error: {shown}") from last_error


CONDITIONAL_ACTIONS = frozenset({"if_selector", "try", "try_each"})


async def dispatch_conditional(
    session: SessionLike,
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
