# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Periodic leader-only housekeeping: reap orphaned browsers + bound the daemon log.

Armed once in the leader (see ``cli.serve._run_leader``) when
``HOUSEKEEPING_INTERVAL_SECONDS`` is enabled. Five jobs run every interval:

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

3. **Reap dead-follower MCP sessions.** ``OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS``
   (off by default) is the only other session-reclaim knob, and it reaps by
   *idle time* — which can't tell a follower that's merely quiet (human
   reading output, a long CI run) from one whose OS process is actually gone.
   This job reaps by *PID liveness* instead: bridge-state.json already carries
   each follower's ``(follower_pid, remote_session_id)`` on every activity
   snapshot, so a follower whose PID no longer exists is unambiguously dead —
   never a live client being quiet — and its leader-side StreamableHTTP
   session (which pins a per-session server task + transport in memory
   indefinitely once abandoned, the multi-GB leak this job exists to contain)
   gets terminated immediately, independent of idle time and safe to run
   unconditionally.

4. **Cap the MCP session table.** Job 3 only reaps sessions whose follower
   *process* is dead; a follower that's alive but storming (each RPC opening a
   fresh session instead of reusing one) can still pile sessions up until the
   leader is at multiple GB. This job is the version-agnostic bound: when the
   live session table exceeds ``OCTOWRIGHT_MCP_MAX_SESSIONS`` (default 256), it
   evicts the most-idle sessions back to the cap — abandoned (silent past the
   tracker TTL) first, a quietly-waiting live session last. Pairs with the
   leader-side new-session rate limit (``http/mcp_flap_guard``) that stops the
   storm at the source; this bounds whatever slips through.

5. **Sweep stale bridge-state tmp debris.** ``bridge_state.record_snapshot`` /
   ``remove_followers`` write via a temp-sibling-then-``os.replace``, which
   normally leaves no tmp file behind — but a process killed between the
   write and the replace (crash, SIGKILL, host restart) orphans one
   permanently. 364 of these (some weeks old) were found and hand-cleaned on
   2026-07-09; nothing swept them automatically, so they'd reaccumulate
   forever. Age-gated well past any real write's lifetime so it can never
   race one still in flight.
"""

from __future__ import annotations

import asyncio
import os
import stat as _stat
from typing import Any

from octowright._tracing import counter, histogram

# Leader + managed-browser resident memory, sampled each housekeeping cycle (noop
# unless telemetry is on). This is the continuous, multi-day RSS signal that the
# synthetic leak harness can only approximate — graph max(scope=total) over days
# to catch a real leak that no CI run is long enough to see. scope=leader|browsers|total.
_PROCESS_RSS = histogram(
    "octowright_process_rss_bytes",
    description="Resident memory of the leader + its browser processes (scope=leader|browsers|total)",
    unit="By",
)

# Leader MCP sessions terminated because pid-liveness found their follower's
# OS process gone. A high value means followers are dying (crashed clients,
# killed terminals) faster than anything else notices — the signal this job
# exists to surface as well as act on.
_FOLLOWER_SESSIONS_REAPED = counter(
    "octowright_follower_session_reaped_total",
    description="Leader MCP sessions terminated because their follower process was found dead (pid-liveness reap)",
)

# Process-lifetime running total, mirroring the OTel counter above but readable
# in-process without a meter/exporter — octowright_status()'s bridge block
# surfaces this so an operator can see the reaper working without grepping
# daemon logs for "reaped_dead_follower_sessions".
_reaped_follower_sessions_total = 0


def get_reaped_follower_session_count() -> int:
    """Total leader MCP sessions reaped by pid-liveness since this leader started."""
    return _reaped_follower_sessions_total


# Leader MCP sessions evicted because the live session table exceeded
# OCTOWRIGHT_MCP_MAX_SESSIONS — the storm-proof memory bound (unlike the
# pid-liveness reaper, this sheds live-but-abandoned sessions a storming
# follower left behind, oldest/most-idle first).
_SESSIONS_EVICTED = counter(
    "octowright_mcp_session_evicted_total",
    description="Leader MCP sessions evicted because the session table exceeded OCTOWRIGHT_MCP_MAX_SESSIONS.",
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
    else:
        if summary["killed"]:
            log.warning(
                "octowright.boot.reaped_orphan_browsers",
                count=len(summary["killed"]),
                pids=summary["killed"],
            )
        if summary["still_alive"] or summary["errors"]:
            log.warning(
                "octowright.boot.orphan_reap_incomplete",
                still_alive=summary["still_alive"],
                errors=summary["errors"],
            )
        if summary["still_alive"]:
            # Preserve the manifest as the only session-level diagnostic for
            # browsers the reaper confirmed are still present.
            return
    _prune_dead_daemon_manifest_entries(log=log)


def _prune_dead_daemon_manifest_entries(*, log: Any) -> None:
    """Drop launch-manifest entries stranded by a dead daemon generation.

    This is independently guarded from process enumeration, while a confirmed
    surviving browser keeps its manifest diagnostic. Without pruning, entries
    accumulate forever because ``remove_session`` only fires on graceful close.
    """
    from octowright.session_manifest import prune_dead_daemon_entries

    try:
        removed = prune_dead_daemon_entries()
    except Exception as exc:  # diagnostics only; never block startup
        log.warning("octowright.boot.manifest_prune_failed", error=repr(exc))
        return
    if removed:
        log.info("octowright.boot.pruned_manifest_entries", count=len(removed), session_ids=removed)


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
        try:
            await _reap_dead_follower_sessions_once(log=log)
        except Exception as exc:
            log.warning("octowright.housekeeping.follower_reap_failed", error=repr(exc))
        try:
            await _enforce_mcp_session_cap_once(log=log)
        except Exception as exc:
            log.warning("octowright.housekeeping.session_cap_failed", error=repr(exc))
        try:
            _sweep_bridge_state_tmp_once(log=log)
        except Exception as exc:
            log.warning("octowright.housekeeping.bridge_tmp_sweep_failed", error=repr(exc))


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


def _dead_pid_or_none(pid_key: str, pid_is_alive: Any) -> int | None:
    """Parse a bridge-state follower key and return its PID if confirmed dead.

    Returns ``None`` for an unparsable key or a PID that's alive (or whose
    liveness can't be determined — ``pid_is_alive`` raising is treated
    conservatively as dead, since an unusable/overflowing PID can't belong to
    a real live follower)."""
    try:
        pid = int(pid_key)
    except (TypeError, ValueError):
        return None
    try:
        alive = pid_is_alive(pid)
    except (OverflowError, ValueError):
        alive = False
    return None if alive else pid


async def _terminate_follower_transport(instances: dict[str, Any], session_id: Any) -> str | None:
    """Terminate + drop ``session_id``'s transport from ``instances`` if present.

    Returns the session id if a session was actually reaped, else ``None``.
    terminate() sets ``is_terminated``, which makes the session's own
    ``run_server`` task skip its usual "pop from _server_instances" cleanup
    (see ``StreamableHTTPSessionManager._handle_stateful_request``) — so
    regardless of whether terminate() already ran (e.g. the opt-in idle
    reaper got there first), we always pop the dict entry ourselves rather
    than leaking it forever."""
    if not isinstance(session_id, str) or not session_id:
        return None
    transport = instances.get(session_id)
    if transport is None:
        return None
    if not transport.is_terminated:
        await transport.terminate()
    instances.pop(session_id, None)
    return session_id


async def _reap_dead_follower_sessions_once(*, log: Any) -> None:
    """Terminate leader-side MCP sessions whose follower process has died.

    Reads bridge-state.json for each follower's ``(follower_pid,
    remote_session_id)``; a PID that ``pid_is_alive`` reports gone means that
    session is abandoned beyond doubt, so it's terminated here-and-now rather
    than waiting on the opt-in idle-time reaper (which can't run this
    conservatively — idle time alone can't distinguish a dead follower from a
    live one that's just quiet). No-ops entirely when this process isn't the
    HTTP-MCP leader (``_session_manager`` is unset), matching how
    ``_apply_mcp_session_idle_timeout`` guards the same attribute.
    """
    from octowright import bridge_state, defaults
    from octowright.http.app import mcp_session_manager
    from octowright.server import mcp as _mcp
    from octowright.singleton import pid_is_alive

    manager = mcp_session_manager(_mcp)
    instances = getattr(manager, "_server_instances", None)
    if not isinstance(instances, dict):
        return

    state = bridge_state.read_state(defaults.BRIDGE_STATE_PATH)
    followers = state.get("followers")
    if not isinstance(followers, dict):
        return

    dead_pids: list[int] = []
    reaped_sessions: list[str] = []
    for pid_key, snap in followers.items():
        if not isinstance(snap, dict):
            continue
        pid = _dead_pid_or_none(pid_key, pid_is_alive)
        if pid is None:
            continue
        dead_pids.append(pid)
        reaped_session_id = await _terminate_follower_transport(instances, snap.get("remote_session_id"))
        if reaped_session_id is not None:
            reaped_sessions.append(reaped_session_id)

    if dead_pids:
        await bridge_state.remove_followers_async(defaults.BRIDGE_STATE_PATH, dead_pids)
    if reaped_sessions:
        log.warning(
            "octowright.housekeeping.reaped_dead_follower_sessions",
            count=len(reaped_sessions),
            session_ids=reaped_sessions,
        )
        _FOLLOWER_SESSIONS_REAPED.add(len(reaped_sessions))
        global _reaped_follower_sessions_total
        _reaped_follower_sessions_total += len(reaped_sessions)


async def _enforce_mcp_session_cap_once(*, log: Any) -> None:
    """Evict leader MCP sessions when the live table exceeds the configured cap.

    The pid-liveness reaper (job 3) only sheds sessions whose follower *process*
    is dead — it can't touch a follower that's alive but churning sessions in a
    storm. This is the version-agnostic memory bound: when the manager's session
    table is over ``OCTOWRIGHT_MCP_MAX_SESSIONS``, evict the most-idle sessions
    back down to the cap (abandoned-before-active ordering via the tracker), so
    the table — and the ~54KB/session it costs — can't grow unbounded no matter
    how a follower misbehaves. No-ops when not the HTTP-MCP leader or when the
    cap is disabled. See ``http/mcp_flap_guard``.
    """
    from octowright.http.mcp_flap_guard import mcp_max_sessions, select_eviction_victims
    from octowright.server import mcp as _mcp

    cap = mcp_max_sessions()
    if cap is None:
        return
    from octowright.http.app import mcp_session_manager

    manager = mcp_session_manager(_mcp)
    instances = getattr(manager, "_server_instances", None)
    if not isinstance(instances, dict):
        return
    over = len(instances) - cap
    if over <= 0:
        return

    from octowright.http.app import get_mcp_session_tracker

    tracker = get_mcp_session_tracker()
    recent = tracker.active_ids() if tracker is not None else set()
    last_seen = tracker.last_seen_snapshot() if tracker is not None else {}
    instance_ids = [str(sid) for sid in instances]
    victims = select_eviction_victims(instance_ids, recent, last_seen, over)

    evicted = await _evict_sessions(instances, victims, tracker)
    if evicted:
        log.warning("octowright.housekeeping.evicted_over_cap_sessions", count=len(evicted), cap=cap)
        _SESSIONS_EVICTED.add(len(evicted))


async def _evict_sessions(instances: dict[str, Any], victims: list[str], tracker: Any) -> list[str]:
    """Terminate each victim session's transport; mark it closed in the tracker.
    Returns the ids actually evicted."""
    evicted: list[str] = []
    for session_id in victims:
        reaped = await _terminate_follower_transport(instances, session_id)
        if reaped is None:
            continue
        evicted.append(reaped)
        if tracker is not None:
            tracker.mark_closed(reaped)
    return evicted


def _sweep_bridge_state_tmp_once(*, log: Any) -> None:
    from octowright import bridge_state, defaults

    removed = bridge_state.sweep_stale_tmp_files(defaults.BRIDGE_STATE_PATH)
    if removed:
        log.warning(
            "octowright.housekeeping.swept_stale_bridge_tmp_files",
            count=len(removed),
        )


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
