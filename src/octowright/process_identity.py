# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tell whether a follower recorded in bridge-state.json is still the process it was.

A follower records ``os.getpid()``, and the leader decides it is dead when that
pid is gone. A pid alone answers the question only when both processes number
pids the same way and the pid has not been reused:

- **A different pid namespace.** A follower started by a sandboxed or
  containerised MCP client (bubblewrap, flatpak, snap, a container sharing the
  home directory and the loopback network) writes to the same state file but
  records a pid from its own namespace. The leader looks that number up in
  its own namespace, where it names nothing (so a live follower's session was
  reaped every cycle) or an unrelated process.
- **pid reuse.** A dead follower's pid recycled by the OS reads as alive.

So on Linux each snapshot also carries the follower's pid-namespace inode and
its start time (``/proc/<pid>/stat`` field 22, clock ticks since boot). A reader
in another namespace cannot judge the pid and leaves the follower alone; a
reader in the same namespace treats a live pid with a different start time as
the dead follower's pid reused. A snapshot without these fields -- an older
follower, or a platform without ``/proc`` -- is judged by pid liveness alone,
as before.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any, Literal

PID_NAMESPACE_KEY = "pid_namespace"
PROCESS_START_KEY = "process_start_ticks"

Verdict = Literal["alive", "dead", "foreign"]


def _linux() -> bool:
    return sys.platform.startswith("linux")


def pid_namespace(pid: int | str = "self") -> int | None:
    """The inode of ``pid``'s pid namespace, or None when it cannot be read."""
    if not _linux():
        return None
    try:
        return os.stat(f"/proc/{pid}/ns/pid").st_ino
    except (OSError, ValueError):
        return None


def process_start_ticks(pid: int | str = "self") -> int | None:
    """``pid``'s start time in clock ticks since boot, or None when unreadable."""
    if not _linux():
        return None
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return None
    return parse_start_ticks(raw)


def parse_start_ticks(raw: str) -> int | None:
    """Field 22 (starttime) of a ``/proc/<pid>/stat`` line, or None."""
    # comm (field 2) may contain spaces and parentheses; fields resume after
    # the LAST ')'. Field 3 is index 0 there, so field 22 is index 19.
    _, sep, rest = raw.rpartition(")")
    if not sep:
        return None
    fields = rest.split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def self_identity() -> dict[str, int]:
    """The identity fields this process records in its own snapshot (none off Linux)."""
    identity: dict[str, int] = {}
    namespace = pid_namespace()
    if namespace is not None:
        identity[PID_NAMESPACE_KEY] = namespace
    start = process_start_ticks()
    if start is not None:
        identity[PROCESS_START_KEY] = start
    return identity


def _int_field(snap: Any, key: str) -> int | None:
    value = snap.get(key) if isinstance(snap, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def follower_verdict(pid: int, snap: Any, is_alive: Callable[[int], bool]) -> Verdict:
    """Judge the follower ``snap`` recorded under ``pid`` from this process.

    ``"foreign"``: it recorded a pid namespace other than this process's, so
    the pid cannot be looked up here; callers must neither reap nor prune it.
    ``"dead"``: the pid is gone, or (same namespace) a process with a different
    start time now holds it. ``"alive"`` otherwise. Exceptions from
    ``is_alive`` propagate, so each caller keeps its own policy for them.
    """
    recorded_ns = _int_field(snap, PID_NAMESPACE_KEY)
    own_ns = pid_namespace() if recorded_ns is not None else None
    if recorded_ns is not None and own_ns is not None and recorded_ns != own_ns:
        return "foreign"
    if not is_alive(pid):
        return "dead"
    same_namespace = recorded_ns is not None and own_ns == recorded_ns
    return "dead" if same_namespace and _pid_reused(pid, snap) else "alive"


def _pid_reused(pid: int, snap: Any) -> bool:
    """True when the live ``pid`` started at another time than the one recorded."""
    recorded_start = _int_field(snap, PROCESS_START_KEY)
    if recorded_start is None:
        return False
    current_start = process_start_ticks(pid)
    return current_start is not None and current_start != recorded_start
