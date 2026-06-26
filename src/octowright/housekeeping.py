# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Periodic leader-only housekeeping: reap orphaned browsers + bound the daemon log.

Armed once in the leader (see ``cli.serve._run_leader``) when
``HOUSEKEEPING_INTERVAL_SECONDS`` is enabled. Two jobs run every interval:

1. **Reap orphaned browsers.** When a Playwright driver dies — crash, OOM, a
   killed daemon generation, or ``octowright restart`` taking out a previous
   leader — its browser windows reparent to init and the pool can no longer
   close them. They pile up in the macOS Dock / Windows tray. The sweep
   SIGTERM/SIGKILLs only those *true* orphans (``process_reaper`` scope
   ``"orphaned"``); a live leader's browsers, whose driver is still alive, are
   never touched, so this is safe even with multiple daemons on one host. The
   long-lived default leader never restarts, so without this loop a driver that
   dies mid-session would leak browsers until the next manual restart.

2. **Bound the daemon log.** The detached daemon redirects fd 2 to a regular
   file (``daemonize._DAEMON_LOG``); that file is only rotated at *spawn* time,
   so a leader running for days writes to it unbounded. When the file fd 2 is
   actually writing to exceeds the cap, truncate it in place (append-mode writes
   resume at offset 0) and leave a breadcrumb. Gated on fd 2 being that exact
   file, so an inline/follower leader (stderr = terminal) or a user-redirected
   stderr is never touched.
"""

from __future__ import annotations

import asyncio
import os
import stat as _stat
from typing import Any

from octowright._tracing import histogram

# Leader + managed-browser resident memory, sampled each housekeeping cycle (noop
# unless telemetry is on). This is the continuous, multi-day RSS signal that the
# synthetic leak harness can only approximate — graph max(scope=total) over days
# to catch a real leak that no CI run is long enough to see. scope=leader|browsers|total.
_PROCESS_RSS = histogram(
    "octowright_process_rss_bytes",
    description="Resident memory of the leader + its browser processes (scope=leader|browsers|total)",
    unit="By",
)


def reap_orphan_browsers_at_boot(*, log: Any) -> None:
    """Kill browsers orphaned by a previous (dead) leader generation.

    Called once per leader startup (first spawn, ``octowright restart``, and the
    follower auto-respawn path all reach it), so a daemon that crashed — taking
    its driver down and reparenting its browser windows to init — no longer
    leaves them piling up until a human notices. Only true orphans (dead-driver,
    reparented) are touched, so a concurrently-live leader's browsers are safe;
    ``daemon_housekeeping`` repeats this for drivers that die mid-session.
    """
    from octowright.process_reaper import reap_orphan_browsers

    try:
        summary = reap_orphan_browsers(scope="orphaned")
    except Exception as exc:
        log.warning("octowright.boot.orphan_reap_failed", error=repr(exc))
        return
    if summary["killed"]:
        log.warning(
            "octowright.boot.reaped_orphan_browsers",
            count=len(summary["killed"]),
            pids=summary["killed"],
        )


def start_housekeeping_task(log: Any) -> asyncio.Task[None] | None:
    """Create the periodic housekeeping task, or ``None`` when disabled.

    Reads ``HOUSEKEEPING_INTERVAL_SECONDS`` here (rather than in the caller) so
    the on/off decision and the task wiring live together. Must be called from a
    running event loop (the leader's).
    """
    from octowright.defaults import HOUSEKEEPING_INTERVAL_SECONDS

    if HOUSEKEEPING_INTERVAL_SECONDS is None:
        return None
    return asyncio.create_task(
        daemon_housekeeping(interval_seconds=HOUSEKEEPING_INTERVAL_SECONDS, log=log),
        name="octowright.housekeeping",
    )


async def daemon_housekeeping(*, interval_seconds: float, log: Any) -> None:
    """Run the housekeeping jobs forever, every ``interval_seconds``.

    Each job is wrapped so a transient failure (a flaky ``ps``, a racing
    truncate) is logged but never crashes the leader or stops the loop.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            _reap_orphans_once(log=log)
        except Exception as exc:
            log.warning("octowright.housekeeping.reap_failed", error=repr(exc))
        try:
            _guard_daemon_log_size(log=log)
        except Exception as exc:
            log.warning("octowright.housekeeping.log_guard_failed", error=repr(exc))
        try:
            _sample_process_rss()
        except Exception as exc:
            log.warning("octowright.housekeeping.rss_sample_failed", error=repr(exc))


def _reap_orphans_once(*, log: Any) -> None:
    from octowright.process_reaper import reap_orphan_browsers

    summary = reap_orphan_browsers(scope="orphaned")
    killed = summary["killed"]
    if killed:
        log.warning(
            "octowright.housekeeping.reaped_orphan_browsers",
            count=len(killed),
            pids=killed,
            still_alive=summary["still_alive"] or None,
        )


def _sample_process_rss() -> None:
    """Record the leader's and its browsers' RSS as a histogram sample.

    Browsers are the leader's descendants (this PID is the leader when the
    housekeeping loop runs), so a concurrent daemon's browsers aren't counted.
    The reads are best-effort (``ps``); a failure is swallowed by the caller."""
    from octowright import sysresources
    from octowright.process_reaper import find_browser_pids

    leader = sysresources.process_rss_bytes([os.getpid()])
    browser_pids = find_browser_pids("descendants", root_pid=os.getpid())
    browsers = sysresources.process_rss_bytes(browser_pids)
    _PROCESS_RSS.record(leader, attributes={"scope": "leader"})
    _PROCESS_RSS.record(browsers, attributes={"scope": "browsers"})
    _PROCESS_RSS.record(leader + browsers, attributes={"scope": "total"})


def _guard_daemon_log_size(*, log: Any) -> None:
    from octowright.daemonize import _DAEMON_LOG, _DAEMON_LOG_MAX_BYTES

    try:
        stderr_stat = os.fstat(2)
    except OSError:
        return
    # Inline/follower leaders point stderr at a terminal, not a regular file.
    if not _stat.S_ISREG(stderr_stat.st_mode):
        return
    if stderr_stat.st_size <= _DAEMON_LOG_MAX_BYTES:
        return
    # Only truncate when fd 2 IS our daemon log — never a file the user
    # redirected stderr to, and never a log that a spawn-time rotation has
    # already swapped out from under us (different inode).
    try:
        if not os.path.samestat(stderr_stat, os.stat(_DAEMON_LOG)):
            return
    except OSError:
        return
    dropped = stderr_stat.st_size
    os.ftruncate(2, 0)
    # fd 2 is opened append-mode, so this write lands at the new EOF (offset 0).
    marker = f"--- octowright: daemon log truncated at {dropped} bytes by housekeeping ---\n"
    os.write(2, marker.encode())
    log.info("octowright.housekeeping.daemon_log_truncated", dropped_bytes=dropped)
