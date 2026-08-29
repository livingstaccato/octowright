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
behaviour for a limit, while this trades hanging forever for failing in
thirty seconds. ``core_io_mixin``'s pre-existing 10s cap on ``content()`` is
the same call, already unconditional.
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
    """
    budget = unbounded_call_timeout_seconds() if timeout is None else timeout
    if budget <= 0:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=budget)
    except TimeoutError as exc:
        raise SessionCallTimeoutError(
            f"{operation} did not answer within {budget}s -- the browser target is "
            "unresponsive. Relaunch this session; other sessions are unaffected."
        ) from exc
