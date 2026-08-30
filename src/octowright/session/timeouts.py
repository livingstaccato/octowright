# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Budgets for the Playwright calls Playwright itself will not bound.

``click``/``type``/``goto`` all accept a ``timeout`` and octowright passes
``DEFAULT_ACTION_TIMEOUT_MS``. ``evaluate``, ``title`` and ``content`` accept
none, so a target that stops answering hangs the calling coroutine forever --
observed on 2026-08-29 as a full test suite wedged for 12.6 hours against a
broken WebKit, with ``page.on("crash")`` silent because a wedged target never
crashes, it just stops replying.

ON by default, unlike this repo's other new quotas: those trade a working
behaviour for a limit, while this trades an unbounded hang of the calling
coroutine for a bounded one. That is a narrower guarantee than "failing in
thirty seconds" -- cancellation releases the awaiting coroutine (and the
session's operation gate) within the budget, but it cannot make Playwright's
driver or the browser process abandon a call already sent over the wire; the
underlying request may still be outstanding after ``bounded()`` raises.
``core_io_mixin``'s pre-existing 10s cap on ``content()`` is the same call,
already unconditional.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS = 30.0

_OFF_TOKENS = frozenset({"0", "off", "never", "none", "disabled", "false", "no"})


class SessionCallTimeoutError(RuntimeError):
    """A Playwright call with no timeout of its own outran its budget.

    Session-scoped, like the operation-gate errors: it means this one target
    stopped answering, never that the MCP transport should be restarted.
    """


def unbounded_call_timeout_seconds() -> float:
    """``OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS`` -- 0.0 means unbounded.

    Unparsable and non-positive values fall back to the default rather than
    disabling the guard: a typo must not silently reintroduce the hang this
    exists to prevent. Disabling is only ever an explicit falsey token.
    """
    raw = os.environ.get("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "").strip().lower()
    if raw in _OFF_TOKENS:
        return 0.0
    if not raw:
        return DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS
    return value


async def bounded(awaitable: Awaitable[T], *, operation: str, timeout: float | None = None) -> T:
    """Await *awaitable* under a budget, raising ``SessionCallTimeoutError``.

    ``timeout=None`` resolves from the environment; ``0.0`` awaits unbounded.
    The operation name is in the message because the raised error is what an
    operator or agent sees -- ``asyncio.TimeoutError`` alone names nothing.

    Uses ``asyncio.timeout()``, NOT ``asyncio.wait_for`` -- this repo has
    already shipped and fixed this exact bug once (see
    ``server/browser/inspect_capture.py``'s ``_capture_before_close``).
    ``wait_for`` runs *awaitable* in a SEPARATE Task via ``ensure_future``,
    and ``SessionOperationGate`` grants re-entry by ``asyncio.current_task()``
    identity -- so if the awaited code re-enters the same session's gate (an
    ARIA scrub, a nested macro call), it would look like a stranger task and
    queue behind the very lease this call is running under, until the queue
    timeout. ``asyncio.timeout()`` sets a deadline on the CURRENT task
    instead of spawning a new one, so task identity is preserved through the
    whole call regardless of what the awaited code does.
    """
    budget = unbounded_call_timeout_seconds() if timeout is None else timeout
    if budget <= 0:
        return await awaitable
    try:
        async with asyncio.timeout(budget):
            return await awaitable
    except TimeoutError as exc:
        raise SessionCallTimeoutError(
            f"{operation} did not answer within {budget}s -- the browser target is "
            "unresponsive. It may still be executing (this is not a crash, and Octowright "
            "does not auto-recover it) -- retry or wait if the work may still finish; "
            "relaunch this session if it stays unresponsive. Other sessions are unaffected."
        ) from exc
