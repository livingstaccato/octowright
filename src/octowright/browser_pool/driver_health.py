# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Detect when the shared Playwright driver (one node process per pool) has died.

The pool keeps a single ``async_playwright().start()`` driver shared by every
browser. If that node process dies — crash, OOM, killed daemon generation — its
stdio pipe closes and every subsequent driver call fails with a connection/pipe
error, which would otherwise brick the whole pool until a restart. ``pool.launch`` uses
``is_driver_dead_error`` to recognise that class of failure, discard the dead
driver, and rebuild it on retry.

The match is on error *text* (Playwright surfaces these as generic
``playwright._impl._errors.Error`` / ``ValueError``), kept deliberately narrow so
ordinary per-launch failures (bad URL, missing binary, navigation error) are NOT
treated as driver death.

**Text is a suspicion, not a verdict.** One marker, "Target page, context or
browser has been closed", is ALSO what Playwright raises when a single browser
process exits during launch (measured on Playwright 1.63: chromium given an
executable that exits at once, or a dead ``--ozone-platform=wayland`` socket,
raises ``TargetClosedError: BrowserType.launch: Target page, context or browser
has been closed`` while the driver stays healthy and every other browser stays
usable). Resetting on that text stopped the shared driver and evicted every live
browser for one browser's problem. So a reset additionally requires
``driver_is_alive`` to say the driver is gone -- see its docstring for what it
checks and what was measured.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

from playwright._impl._errors import Error as PlaywrightError
from playwright._impl._errors import TargetClosedError
from provide.telemetry import get_logger

log = get_logger(__name__)

#: Bound on the liveness round trip. Measured on Playwright 1.63: 0.57ms median,
#: 4.6ms max idle; 1.1ms median, 7.4ms max with three pages navigating. Two
#: seconds is ~300x the worst observed, so a busy event loop does not read as a
#: dead driver -- a false "dead" is the bug this probe exists to prevent.
DRIVER_PROBE_TIMEOUT_SECONDS: Final = 2.0

# Substrings that only appear when the driver connection/transport itself is gone,
# not when a single browser action fails. Lower-cased compare.
_DRIVER_DEAD_MARKERS = (
    "connection closed",
    "target page, context or browser has been closed",
    "i/o operation on closed file",
    "browser has been closed",
    "transport closed",
    "pipe closed",
)


def is_driver_dead_error(exc: BaseException) -> bool:
    """True when ``exc`` indicates the shared Playwright driver connection died
    (as opposed to an ordinary per-launch failure)."""
    text = str(exc).lower()
    return any(marker in text for marker in _DRIVER_DEAD_MARKERS)


def _connection_of(pw: Any) -> Any:
    """The Playwright ``Connection`` behind a public ``Playwright`` handle.

    Private API, read defensively: an ``AttributeError`` propagates to the
    caller, which falls back to the text verdict."""
    return pw._impl_obj._connection


def _flagged_dead(connection: Any) -> bool:
    """Authoritative local evidence that the driver is gone, no round trip.

    Measured on 1.63: after ``Playwright.stop()`` the connection's
    ``_closed_error`` is set; after the node driver is SIGKILLed,
    ``_closed_error`` stays ``None`` but the transport's ``on_error_future`` is
    done and the process has a ``returncode``. A hung (SIGSTOPped) driver
    shows none of these -- only the round trip catches it."""
    if getattr(connection, "_closed_error", None) is not None:
        return True
    transport = getattr(connection, "_transport", None)
    on_error = getattr(transport, "on_error_future", None)
    if on_error is not None and on_error.done():
        _consume(on_error)  # read it, so asyncio does not log it as never retrieved
        return True
    proc = getattr(transport, "_proc", None)
    return getattr(proc, "returncode", None) is not None


def _consume(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()


async def driver_is_alive(pw: Any, *, timeout: float | None = None) -> bool:
    """True only when the shared driver demonstrably answers.

    1. No handle, or Playwright internals this cannot read -> ``False``
       (dead): the caller then behaves as it did before this check existed.
    2. ``_flagged_dead`` -> ``False``.
    3. A real protocol round trip, bounded by ``timeout``
       (``DRIVER_PROBE_TIMEOUT_SECONDS``): ``localUtils.traceDiscarded`` with
       an empty ``stacksId``. The driver returns before any lookup for a falsy
       id, so it cannot touch a real tracing session. An answer -- success, or
       a protocol error the driver sent back -- is alive. A transport error
       (``Connection closed while reading from the driver``, measured after
       SIGKILL, <1ms), ``TargetClosedError``, or no answer in time is dead.

    The bound uses ``asyncio.wait``, NOT ``asyncio.wait_for``: cancelling a
    Playwright channel send runs ``Connection._abort``, which waits for the
    driver to acknowledge -- measured: ``wait_for(..., 0.5)`` against a
    SIGSTOPped driver did not return until the driver was resumed. The
    unanswered send is left to finish (or fail) on its own.
    """
    if pw is None:
        return False
    bound = DRIVER_PROBE_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        connection = _connection_of(pw)
        if _flagged_dead(connection):
            return False
        channel = connection.local_utils._channel
        task = asyncio.ensure_future(channel.send("traceDiscarded", None, {"stacksId": ""}))
    except Exception as exc:
        log.warning("octowright.pool.driver_probe_unavailable", error=repr(exc))
        return False
    done, _ = await asyncio.wait({task}, timeout=bound)
    if not done:
        task.add_done_callback(_consume)
        log.info("octowright.pool.driver_probe_timed_out", timeout_s=bound)
        return False
    error = task.exception()
    if error is None:
        return True
    return isinstance(error, PlaywrightError) and not isinstance(error, TargetClosedError)
