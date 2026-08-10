# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Prune a Chromium profile lock whose owner is gone.

Chromium marks a user-data-dir as in-use with three symlinks: ``SingletonLock``
(pointing at ``<hostname>-<pid>``), ``SingletonSocket`` (into the OS temp dir)
and ``SingletonCookie``. They are how a second Chromium knows not to scribble
over a profile a first one is still using.

A browser that dies without an orderly shutdown leaves them behind — and on
macOS the socket they reference lives under ``/var/folders/.../T/``, which a
cache/temp clear wipes while the profile keeps the now-dangling lock. Either
way the next launch of that profile fails with::

    BrowserType.launch_persistent_context: Opening in existing browser session.
    This usually means that the profile is already in use by another instance
    of Chromium.

and stays broken until someone deletes the files by hand — a persistent profile
(with its saved logins) is effectively bricked by a temp-dir cleanup.

Pruning is deliberately conservative, because removing a *live* lock is the one
thing worse than leaving a stale one: two Chromiums sharing a profile corrupt
it. We remove only when the lock names a pid on **this** host that is confirmed
no longer running. A lock from another hostname (profile on shared storage), an
unreadable target, or a live pid is always left alone.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from provide.telemetry import get_logger

log = get_logger(__name__)

_LOCK_ENTRIES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def _owning_pid(lock: Path) -> int | None:
    """The local pid recorded in ``SingletonLock``, or None when it isn't ours.

    The target is ``<hostname>-<pid>``. A hostname that isn't this machine says
    nothing about whether the owner is alive here, so it is treated as unknown
    rather than stale.
    """
    try:
        target = os.readlink(lock)
    except OSError:
        return None
    host, _, pid_text = target.rpartition("-")
    if not host or host != socket.gethostname():
        return None
    try:
        return int(pid_text)
    except ValueError:
        return None


def _pid_is_running(pid: int) -> bool:
    """True when ``pid`` exists. A permission error means it exists but belongs
    to another user — still running, so still not ours to prune."""
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def prune_stale_singleton_locks(user_data_dir: Path) -> list[str]:
    """Remove Chromium's lock entries when their owning process is gone.

    Returns the names removed (empty when nothing was stale). Never raises: a
    profile that cannot be tidied should still be attempted, so the launch
    surfaces Chromium's own error rather than ours.
    """
    lock = user_data_dir / "SingletonLock"
    if not lock.is_symlink() and not lock.exists():
        return []

    pid = _owning_pid(lock)
    if pid is None or _pid_is_running(pid):
        return []

    removed: list[str] = []
    for name in _LOCK_ENTRIES:
        entry = user_data_dir / name
        if not entry.is_symlink() and not entry.exists():
            continue
        try:
            entry.unlink()
        except OSError as exc:
            log.debug("octowright.profile.lock_unlink_failed", entry=str(entry), error=repr(exc))
            continue
        removed.append(name)

    if removed:
        log.info(
            "octowright.profile.stale_lock_pruned",
            user_data_dir=str(user_data_dir),
            owner_pid=pid,
            removed=removed,
        )
    return removed


__all__ = ["prune_stale_singleton_locks"]
