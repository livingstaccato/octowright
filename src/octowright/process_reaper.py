# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Find and kill orphaned Playwright-managed browser processes.

Playwright launches ``ms-playwright/<engine>-<rev>/...`` subprocesses for each
browser. Under normal teardown the asyncio listeners in
``browser_pool.listeners`` evict each session and Playwright closes its own
subprocesses. When the parent Python process dies abruptly — daemon SIGKILL,
recording-script crash, OS sleep, etc. — those subprocesses get reparented
to init/services.exe and persist as orphans. They accumulate over a session,
eating RAM and (more visibly) cluttering the macOS Dock or Windows tray.

Two entry points:

* ``find_browser_pids(scope)`` returns the live PIDs to act on. ``scope`` is
  one of ``"descendants"`` (only processes whose ancestor chain contains a
  given root PID — used at daemon shutdown so a concurrent daemon's browsers
  aren't touched), ``"orphaned"`` (only browsers whose owning driver has died
  and reparented them to init — the safe sweep run periodically and at leader
  boot, which never touches a live daemon's browsers), or ``"all"`` (every
  ms-playwright/{chromium,firefox,webkit} on the box — used by the explicit
  cleanup CLI and ``octowright restart``).
* ``reap_orphan_browsers(scope, dry_run=False, root_pid=None)`` does the
  kill, returns a summary suitable for both the CLI and the daemon log.

No psutil dependency — uses ``ps`` + ``os.kill`` on POSIX and ``tasklist`` +
``taskkill`` on Windows, both of which ship with the OS.
"""

from __future__ import annotations

import csv
import io
import os
import signal
import subprocess
import sys
import time
from typing import Any, Literal, TypedDict

Scope = Literal["descendants", "all", "orphaned"]

# Windows has no SIGKILL; on POSIX the daemon-shutdown path uses SIGTERM →
# grace → SIGKILL escalation. KILL_SIGNAL exists for the POSIX escalation;
# on Windows the platform-specific killer ignores the signum and always
# issues a forced taskkill, so the constant is irrelevant there.
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


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_browser_command(command: str) -> bool:
    # Playwright command lines use ``/`` on POSIX and ``\`` on Windows;
    # normalize so a single substring list catches both.
    normalized = command.replace("\\", "/").lower()
    return any(needle in normalized for needle in _BROWSER_PATH_SUBSTRINGS)


def _list_processes() -> list[tuple[int, int, str]]:
    """Return ``[(pid, ppid, command_line), ...]`` for every live process."""
    if _is_windows():
        return _list_processes_windows()
    return _list_processes_posix()


def _list_processes_posix() -> list[tuple[int, int, str]]:
    # Fixed `ps` argv, PATH-resolved system binary, no shell.
    out = subprocess.run(  # nosec B603 B607
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


def _list_processes_windows() -> list[tuple[int, int, str]]:
    # PowerShell CSV: ProcessId,ParentProcessId,CommandLine. ``wmic`` is
    # deprecated on recent Windows; ``Get-CimInstance`` is the current way.
    # ConvertTo-Csv -NoTypeInformation drops the leading type line, leaving
    # header + one row per process.
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | "
        "ConvertTo-Csv -NoTypeInformation"
    )
    # Fixed PowerShell argv, literal script body, no shell metachars.
    out = subprocess.run(  # nosec B603 B607
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[int, int, str]] = []
    reader = csv.reader(io.StringIO(out.stdout))
    try:
        header = next(reader)
    except StopIteration:
        return rows
    try:
        pid_idx = header.index("ProcessId")
        ppid_idx = header.index("ParentProcessId")
        cmd_idx = header.index("CommandLine")
    except ValueError:
        return rows
    for row in reader:
        if len(row) <= max(pid_idx, ppid_idx, cmd_idx):
            continue
        try:
            pid = int(row[pid_idx])
            ppid = int(row[ppid_idx])
        except ValueError:
            continue
        rows.append((pid, ppid, row[cmd_idx] or ""))
    return rows


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


def _is_orphaned_browser(ppid: int, live_pids: frozenset[int]) -> bool:
    """Decide whether a Playwright browser whose parent is ``ppid`` is orphaned.

    A healthy browser is a direct child of a live Playwright driver process
    (itself a child of the daemon), so its ``ppid`` is always a live, non-init
    pid. When the driver (or the whole daemon generation) dies, the kernel
    reparents the browser to init — ``ppid`` becomes ``1`` on POSIX — or, on
    Windows, leaves a stale ``ParentProcessId`` that no longer maps to any live
    process. Either way the pool can no longer drive or close it: it is an
    orphan that lingers in the Dock/tray. ``ppid <= 1`` catches the POSIX
    reparent (1 = init, 0 = the unreachable kernel/idle pid); ``ppid not in
    live_pids`` catches the stale-parent case. A browser whose driver is still
    alive never matches, so this is safe to run even when another daemon is
    live on the same host — its browsers are left untouched.
    """
    return ppid <= 1 or ppid not in live_pids


def _orphaned_browser_pids(table: list[tuple[int, int, str]], candidate_pids: list[int]) -> list[int]:
    """Subset of ``candidate_pids`` whose owning driver has died (see
    ``_is_orphaned_browser``)."""
    live_pids = frozenset(pid for pid, _ppid, _cmd in table)
    ppid_by_pid = {pid: ppid for pid, ppid, _cmd in table}
    return [pid for pid in candidate_pids if _is_orphaned_browser(ppid_by_pid[pid], live_pids)]


def find_browser_pids(scope: Scope, *, root_pid: int | None = None) -> list[int]:
    """Return live Playwright-managed browser PIDs matching ``scope``.

    * ``all`` — every ms-playwright/{chromium,firefox,webkit} process on the box.
    * ``orphaned`` — only those whose owning driver has died (reparented to
      init / stale parent); see ``_is_orphaned_browser``. Safe with concurrent
      daemons. Ignores ``root_pid``.
    * ``descendants`` — only processes under ``root_pid`` (required for this
      scope).
    """
    table = _list_processes()
    candidate_pids = [pid for pid, _ppid, cmd in table if _is_browser_command(cmd)]
    if scope == "all":
        return candidate_pids
    if scope == "orphaned":
        return _orphaned_browser_pids(table, candidate_pids)
    if root_pid is None:
        raise ValueError("scope='descendants' requires root_pid")
    descendants = _descendants_of(root_pid, table)
    return [pid for pid in candidate_pids if pid in descendants]


def _kill_pid(pid: int, *, signum: int) -> tuple[bool, str | None]:
    if _is_windows():
        return _kill_pid_windows(pid)
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


def _kill_pid_windows(pid: int) -> tuple[bool, str | None]:
    # taskkill exit codes: 0 success; 128 = process not found (treat as
    # ProcessLookupError-equivalent). Anything else is a real failure.
    # Fixed `taskkill` argv, integer pid arg, no shell.
    out = subprocess.run(  # nosec B603 B607
        ["taskkill", "/F", "/PID", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    if out.returncode == 0 or out.returncode == 128:
        return True, None
    message = (out.stderr or out.stdout or "").strip() or f"taskkill exit {out.returncode}"
    return False, message


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
    """Send SIGTERM, wait briefly, then SIGKILL anything still alive.

    On Windows there is no graceful equivalent for opaque subprocesses, so
    both stages issue a forced ``taskkill /F``. The two-pass shape is kept
    for diagnostic parity (the ``errors`` list still labels each stage).
    """
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
        await pool.close_all(force=True)
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
