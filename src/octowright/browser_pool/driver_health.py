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

The match is on error *text* (Playwright surfaces these as its generic
``Error`` / ``ValueError``), kept deliberately narrow so
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
``driver_confirmed_dead`` to find evidence the driver is gone -- see its
docstring for what it checks and what was measured.
"""

from __future__ import annotations

import asyncio
from typing import Any, Final

from playwright.async_api import Error as PlaywrightError
from provide.telemetry import get_logger

log = get_logger(__name__)

#: Bound on the liveness round trip. Measured on Playwright 1.63: 0.57ms median,
#: 4.6ms max idle; 1.1ms median, 7.4ms max with three pages navigating; 243ms
#: max while the driver zipped a trace and flushed a 120MB embedded HAR. No
#: answer within it is NOT death (see ``driver_confirmed_dead``), so this only
#: bounds how long a launch failure waits before it is re-raised.
DRIVER_PROBE_TIMEOUT_SECONDS: Final = 2.0

#: Bound on ``Playwright.stop()`` during a reset. Measured: 0.1ms against a
#: SIGKILLed driver; never returns against a SIGSTOPped one (pending after 3s),
#: and returns 10ms after that process is killed.
DRIVER_STOP_TIMEOUT_SECONDS: Final = 5.0

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


def _is_target_closed(error: BaseException) -> bool:
    """``TargetClosedError`` by class NAME: the class is not exported by
    ``playwright.async_api``, and importing it from ``playwright._impl`` would
    turn a Playwright upgrade that moves it into a daemon that cannot start --
    the opposite of this module's soft fallback."""
    return any(cls.__name__ == "TargetClosedError" for cls in type(error).__mro__)


def _start_probe(connection: Any) -> asyncio.Future[Any]:
    """Send the no-op round trip WITHOUT consuming a stored listener error.

    ``Channel._inner_send`` raises -- and clears -- ``Connection._error`` (an
    exception a sync event listener raised, saved "to throw at the next API
    call") before sending anything. Measured on 1.63: a ``console`` listener
    raising ``ValueError`` made the probe report a healthy driver dead and
    swallowed the error the caller's next call was meant to see. So it is
    lifted off for the send and put back once the send has started (the check
    is the send's first step, which runs before ``asyncio.wait`` returns)."""
    stored = getattr(connection, "_error", None)
    if stored is not None:
        connection._error = None
    try:
        task = asyncio.ensure_future(connection.local_utils._channel.send("traceDiscarded", None, {"stacksId": ""}))
    except BaseException:
        _restore_error(connection, stored)
        raise
    # Always retrieved, whoever stops waiting first -- a caller cancelled
    # mid-wait leaves the task running, and asyncio would log its exception.
    task.add_done_callback(_consume)
    task.add_done_callback(lambda _t: _restore_error(connection, stored))
    return task


def _restore_error(connection: Any, stored: BaseException | None) -> None:
    # A newer listener error wins, as it would in Playwright itself.
    if stored is not None and getattr(connection, "_error", None) is None:
        connection._error = stored


async def driver_confirmed_dead(pw: Any, *, timeout: float | None = None) -> bool:
    """True only on EVIDENCE that the shared driver is gone.

    1. No handle, or Playwright internals this cannot read -> ``True``: the
       caller then behaves as it did before this check existed (reset on the
       text verdict), not "never reset".
    2. ``_flagged_dead`` -> ``True``.
    3. A real protocol round trip, bounded by ``timeout``
       (``DRIVER_PROBE_TIMEOUT_SECONDS``): ``localUtils.traceDiscarded`` with
       an empty ``stacksId``. The driver returns before any lookup for a falsy
       id, so it cannot touch a real tracing session. ``TargetClosedError`` or
       a transport error (``Connection closed while reading from the driver``,
       measured after SIGKILL, <1ms; it always leaves ``_flagged_dead`` true)
       is dead. An answer -- success, or a protocol error the driver sent
       back -- is alive. Any other exception is a listener error that reached
       the send in the window before it started (see ``_start_probe``); it is
       put back and the round trip tried once more.
    4. **No answer in time is NOT confirmed death** -> ``False``. Every real
       death measured leaves a local flag (step 2) -- after SIGKILL, after
       ``stop()`` -- so a silent driver is one that is busy (a trace zip or HAR
       flush stalled the round trip up to 243ms, measured) or hung (SIGSTOPped).
       A reset evicts every live browser, which is the wrong answer for a busy
       driver; and a hung one is not where this is reached from, since a launch
       against it hangs rather than raising a dead-driver error.

    The bound uses ``asyncio.wait``, NOT ``asyncio.wait_for``: cancelling a
    Playwright channel send runs ``Connection._abort``, which waits for the
    driver to acknowledge -- measured: ``wait_for(..., 0.5)`` against a
    SIGSTOPped driver did not return until the driver was resumed. The
    unanswered send is left to finish (or fail) on its own.
    """
    if pw is None:
        return True
    bound = DRIVER_PROBE_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = asyncio.get_running_loop().time() + bound
    for _attempt in range(2):
        try:
            connection = _connection_of(pw)
            if _flagged_dead(connection):
                return True
            task = _start_probe(connection)
        except Exception as exc:
            log.warning("octowright.pool.driver_probe_unavailable", error=repr(exc))
            return True
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if not done or task.cancelled():
            log.info("octowright.pool.driver_probe_unanswered", timeout_s=bound, cancelled=bool(done))
            return False
        verdict = _answer_verdict(task.exception(), connection)
        if verdict is not None:
            return verdict
    return False


def _answer_verdict(error: BaseException | None, connection: Any) -> bool | None:
    """Confirmed-dead verdict for a probe that finished; ``None`` = try again.

    ``None`` is a listener error that reached the send in the window before it
    started (see ``_start_probe``): it is put back for the caller's next call."""
    if error is None:
        return False
    if _is_target_closed(error) or _flagged_dead(connection):
        return True
    if isinstance(error, PlaywrightError):
        return False  # the driver answered, with a refusal
    connection._error = error  # newest wins, as in Playwright's own handler
    return None


async def stop_driver(pw: Any) -> None:
    """``pw.stop()``, bounded; on timeout kill the driver process so the stop
    (and every call still waiting on that driver) finishes.

    A reset clears the pool's handle before stopping, so nothing waits on this
    but the reset itself -- yet an unbounded stop of a hung driver held the
    launch that triggered the reset forever (measured: pending after 3s
    against a SIGSTOPped driver; done 10ms after the process was killed)."""
    task = asyncio.ensure_future(pw.stop())
    task.add_done_callback(_consume)
    done, _ = await asyncio.wait({task}, timeout=DRIVER_STOP_TIMEOUT_SECONDS)
    if done:
        if not task.cancelled() and task.exception() is not None:
            log.debug("octowright.pool.driver_stop_failed", error=repr(task.exception()))
        return
    log.warning("octowright.pool.driver_stop_timed_out", timeout_s=DRIVER_STOP_TIMEOUT_SECONDS)
    try:
        pw._impl_obj._connection._transport._proc.kill()
    except (AttributeError, ProcessLookupError, OSError) as exc:
        log.warning("octowright.pool.driver_kill_failed", error=repr(exc))
        return
    await asyncio.wait({task}, timeout=DRIVER_STOP_TIMEOUT_SECONDS)
