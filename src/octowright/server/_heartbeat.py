# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-side progress heartbeat for long-running tool calls.

The follower bridge injects a synthetic ``progressToken`` into every
``tools/call`` and re-arms that request's in-flight deadline every time it sees a
``notifications/progress`` carrying the token (``proxy_supervisor._rearm_deadline``).
That re-arm machinery is the intended defence against a slow-but-alive tool call
tripping the flat ``BRIDGE_REQUEST_TIMEOUT_SECONDS`` deadline — but nothing on the
leader ever emitted the pings, so it never fired. A genuinely-working call that
outran its static budget surfaced to the agent as a spurious "Octowright
disconnected", and (per ``BRIDGE_ERROR_GUIDANCE``) the agent then told the user to
reconnect a perfectly healthy server.

This wrapper emits the missing pings. While a tool handler runs, a background task
sends progress on the injected token every ``HEARTBEAT_INTERVAL_SECONDS``. The
first ping lands before the flat 20s deadline, so EVERY tool — even those with no
per-tool ``BRIDGE_TOOL_TIMEOUTS`` override — has its deadline re-armed and stays
alive as long as the leader event loop is alive to run the heartbeat. The three
failure modes now resolve predictably:

* **Slow but alive** — pings flow, deadline re-arms, the call completes. No false
  disconnect.
* **Leader event loop wedged / dead** — the heartbeat task can't run either, so no
  pings, so the deadline expires fast. A real problem is surfaced quickly.
* **Handler wedged past its own internal timeout** — the pings stop at
  ``HEARTBEAT_MAX_SECONDS``, bounding the worst-case single-call hang instead of
  keeping a stuck call alive forever.

Applied as the OUTERMOST tool wrapper in ``server/_state.py`` so it also keeps a
follower alive while it awaits an in-progress idempotency entry (a resend race).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from provide.telemetry import get_logger

from octowright.server._request_context import current_meta_value, current_session

log = get_logger(__name__)

# Cadence of the keepalive pings. MUST be below the follower's flattest in-flight
# budget (``BRIDGE_REQUEST_TIMEOUT_SECONDS``, default 20s) so the first ping re-arms
# the deadline before it expires — that is what covers tools with no per-tool
# override. Lives here, not defaults.py (at its LOC ceiling), mirroring how
# proxy_supervisor keeps its own SUSPEND_THRESHOLD_SECONDS const.
HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("OCTOWRIGHT_HEARTBEAT_INTERVAL_SECONDS", "8"))
# Absolute ceiling on how long the heartbeat keeps one call alive. Past this the
# pings stop and the follower deadline finally expires — the backstop for a handler
# wedged past its own internal timeout (a real bug), so a single tool call can hang
# at most this long. MUST exceed the longest legitimate single tool call (a big
# ``macro_run_sequence``) or a real long run would be cut and the agent's retry
# would double-execute the side effect.
HEARTBEAT_MAX_SECONDS = float(os.environ.get("OCTOWRIGHT_HEARTBEAT_MAX_SECONDS", "600"))


def _current_progress_token() -> Any:
    """The follower-injected ``progressToken`` for the in-flight request, or None
    when there's no request context / no token (nothing to keep alive)."""
    return current_meta_value("progress_token", "progressToken")


def _current_session() -> Any:
    return current_session()


async def _beat(
    session: Any,
    token: Any,
    tool_name: str,
    *,
    interval: float,
    ceiling: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Emit progress on ``token`` every ``interval`` seconds until cancelled, the
    send fails (session gone), or ``ceiling`` is reached. Progress increases each
    ping (MCP requires monotonic progress); ``total`` stays None (unknown)."""
    start = monotonic()
    progress = 0.0
    while True:
        await sleep(interval)
        elapsed = monotonic() - start
        if elapsed >= ceiling:
            log.warning("octowright.heartbeat.ceiling_reached", tool=tool_name, elapsed_seconds=round(elapsed, 1))
            return
        progress += 1
        try:
            await session.send_progress_notification(
                progress_token=token,
                progress=progress,
                total=None,
                message=f"{tool_name} still working ({int(elapsed)}s)",
            )
        except Exception as exc:
            # The session died (reconnect / teardown). Nothing to keep alive; stop
            # quietly — the reconnect path owns recovery.
            log.debug("octowright.heartbeat.send_failed", tool=tool_name, error=repr(exc))
            return


def _progress_heartbeat(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an async tool so its in-flight bridge deadline is kept alive by periodic
    MCP progress while it runs. Sync tools and calls without a progressToken/session
    pass straight through. ``functools.wraps`` preserves the signature so the server's
    Context injection and input schema still resolve through this wrapper.
    """
    if not asyncio.iscoroutinefunction(fn):
        return fn

    tool_name = getattr(fn, "__name__", "")

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _current_progress_token()
        session = _current_session()
        if token is None or session is None:
            return await fn(*args, **kwargs)
        beat = asyncio.ensure_future(
            _beat(
                session,
                token,
                tool_name,
                interval=HEARTBEAT_INTERVAL_SECONDS,
                ceiling=HEARTBEAT_MAX_SECONDS,
            )
        )
        try:
            return await fn(*args, **kwargs)
        finally:
            beat.cancel()
            # Await the cancelled beat so it can't outlive the call (no "Task was
            # destroyed but it is pending" leak). Suppress only its own
            # CancelledError; any real exception from fn already propagates.
            with contextlib.suppress(asyncio.CancelledError):
                await beat

    return wrapper
