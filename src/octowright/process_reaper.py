# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Find and kill orphaned Playwright-managed browser processes.

Playwright launches `ms-playwright/<engine>-<rev>/...` subprocesses for each
browser. Under normal teardown the asyncio listeners in
``browser_pool.listeners`` evict each session and Playwright closes its own
subprocesses. When the parent Python process dies abruptly — daemon SIGKILL,
recording-script crash, OS sleep, etc. — those subprocesses get reparented
to launchd/init and persist as orphans. They accumulate over a session,
eating RAM and (more visibly) cluttering the macOS Dock.

Two entry points:

* ``find_browser_pids(scope)`` returns the live PIDs to act on. ``scope`` is
  either ``"descendants"`` (only processes whose ancestor chain contains a
  given root PID — used at daemon shutdown so a concurrent daemon's browsers
  aren't touched) or ``"all"`` (every ms-playwright/{chromium,firefox,webkit}
  on the box — used by the explicit cleanup CLI).
* ``reap_orphan_browsers(scope, dry_run=False, root_pid=None)`` does the
  kill, returns a summary suitable for both the CLI and the daemon log.

No psutil dependency — pure ``ps``/``kill`` so this works in any environment
the daemon itself runs in.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Literal, TypedDict

Scope = Literal["descendants", "all"]

# Windows has no SIGKILL — TerminateProcess is invoked for any signum, so
# SIGTERM is the strongest available signal. POSIX keeps the SIGTERM →
# grace → SIGKILL escalation.
KILL_SIGNAL: int = getattr(signal, "SIGKILL", signal.SIGTERM)


class ReapSummary(TypedDict):
    killed: list[int]
    still_alive: list[int]
    errors: list[dict[str, str]]


_BROWSER_PATH_SUBSTRINGS = (
    "ms-playwright/chromium",
    "ms-playwright/firefox",
    "ms-playwright/webkit",
)


def _ps_pid_ppid_cmd() -> list[tuple[int, int, str]]:
    """Return ``[(pid, ppid, command_line), ...]`` for every live process."""
    # -A: all processes; -o: custom format with ppid + full args.
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,command="],
        check=False,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[int, int, str]] = []
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append((pid, ppid, parts[2]))
    return rows


def _is_browser_command(command: str) -> bool:
    return any(needle in command for needle in _BROWSER_PATH_SUBSTRINGS)


def _descendants_of(root_pid: int, table: list[tuple[int, int, str]]) -> set[int]:
    """BFS down the process tree from ``root_pid``."""
    children_by_parent: dict[int, list[int]] = {}
    for pid, ppid, _cmd in table:
        children_by_parent.setdefault(ppid, []).append(pid)
    seen: set[int] = set()
    frontier = [root_pid]
    while frontier:
        current = frontier.pop()
        for child in children_by_parent.get(current, []):
            if child in seen:
                continue
            seen.add(child)
            frontier.append(child)
    return seen


def find_browser_pids(scope: Scope, *, root_pid: int | None = None) -> list[int]:
    """Return live Playwright-managed browser PIDs matching ``scope``.

    ``descendants`` requires ``root_pid``; ``all`` ignores it.
    """
    table = _ps_pid_ppid_cmd()
    candidate_pids = [pid for pid, _ppid, cmd in table if _is_browser_command(cmd)]
    if scope == "all":
        return candidate_pids
    if root_pid is None:
        raise ValueError("scope='descendants' requires root_pid")
    descendants = _descendants_of(root_pid, table)
    return [pid for pid in candidate_pids if pid in descendants]


def _kill_pid(pid: int, *, signum: int) -> tuple[bool, str | None]:
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        # Already gone — that's the desired end state.
        return True, None
    except PermissionError as exc:
        return False, repr(exc)
    except OSError as exc:
        return False, repr(exc)
    return True, None


def _signal_pids(pids: list[int], signum: int, stage: str) -> list[dict[str, str]]:
    """SIGTERM/SIGKILL each pid; return a per-pid error record list."""
    errors: list[dict[str, str]] = []
    for pid in pids:
        ok, err = _kill_pid(pid, signum=signum)
        if not ok and err is not None:
            errors.append({"pid": str(pid), "stage": stage, "error": err})
    return errors


def reap_orphan_browsers(
    scope: Scope,
    *,
    root_pid: int | None = None,
    dry_run: bool = False,
    grace_seconds: float = 0.4,
) -> ReapSummary:
    """Send SIGTERM, wait briefly, then SIGKILL anything still alive."""
    pids = find_browser_pids(scope, root_pid=root_pid)
    if dry_run or not pids:
        return ReapSummary(killed=[], still_alive=pids, errors=[])

    errors = _signal_pids(pids, signal.SIGTERM, "sigterm")
    time.sleep(grace_seconds)
    survivors = [pid for pid in pids if pid in find_browser_pids(scope, root_pid=root_pid)]
    errors.extend(_signal_pids(survivors, KILL_SIGNAL, "sigkill"))

    final = find_browser_pids(scope, root_pid=root_pid)
    return ReapSummary(
        killed=[pid for pid in pids if pid not in final],
        still_alive=[pid for pid in pids if pid in final],
        errors=errors,
    )


async def reap_descendant_browsers_on_shutdown(pool: Any, *, log: Any) -> None:
    """Daemon shutdown hook. Close pool sessions, then sweep descendants.

    Restricted to descendants of the current PID so a concurrent daemon's
    browsers aren't touched. Best-effort; logs but never raises.
    """
    try:
        await pool.close_all()
    except Exception as exc:
        log.warning("octowright.shutdown.pool_close_failed", error=repr(exc))
    try:
        summary = reap_orphan_browsers(scope="descendants", root_pid=os.getpid())
    except Exception as exc:
        log.warning("octowright.shutdown.browser_reap_failed", error=repr(exc))
        return
    if summary["killed"] or summary["still_alive"] or summary["errors"]:
        log.info(
            "octowright.shutdown.browsers_reaped",
            killed=summary["killed"],
            still_alive=summary["still_alive"],
            errors=summary["errors"],
        )
