# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pruning Chromium's stale profile lock before opening a persistent context.

Chromium marks a profile as in-use with three entries in the user-data-dir:
``SingletonLock`` (a symlink to ``<hostname>-<pid>``), ``SingletonSocket`` (a
symlink into the OS temp dir) and ``SingletonCookie``. If the browser dies
without cleaning up — or, as observed on macOS, the temp dir holding the socket
is cleared out from under it — the lock survives with nothing behind it, and
every later launch of that profile fails with:

    BrowserType.launch_persistent_context: Opening in existing browser session.

The profile is then unusable until someone deletes the files by hand. A dead
owning pid is unambiguous enough to prune automatically; a live one never is.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from octowright.browser_pool.singleton_locks import prune_stale_singleton_locks

_ENTRIES = ("SingletonLock", "SingletonSocket", "SingletonCookie")

# Chromium writes these only on POSIX, and the liveness probe is destructive on
# Windows (os.kill routes to TerminateProcess there), so pruning is a no-op on
# Windows by design — asserted separately below.
posix_only = pytest.mark.skipif(os.name == "nt", reason="Singleton* locks are POSIX-only")


def _write_lock(profile_dir: Path, *, pid: int, socket_path: Path | None, host: str | None = None) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    hostname = socket.gethostname() if host is None else host
    (profile_dir / "SingletonLock").symlink_to(f"{hostname}-{pid}")
    (profile_dir / "SingletonSocket").symlink_to(str(socket_path) if socket_path else "/nonexistent/SingletonSocket")
    (profile_dir / "SingletonCookie").symlink_to("1234567890")


def _dead_pid() -> int:
    """A pid that is certainly not running: start a child, wait for it to exit.

    Uses subprocess rather than ``os.fork`` so the test runs on Windows too —
    ``fork`` is POSIX-only, and the pruning logic itself is platform-neutral.
    """
    proc = subprocess.Popen([sys.executable, "-c", ""])  # nosec B603
    proc.wait()
    return proc.pid


@posix_only
def test_prunes_lock_whose_owner_is_gone(tmp_path: Path) -> None:
    profile = tmp_path / "chromium"
    _write_lock(profile, pid=_dead_pid(), socket_path=None)

    removed = prune_stale_singleton_locks(profile)

    assert sorted(removed) == sorted(_ENTRIES)
    for entry in _ENTRIES:
        assert not (profile / entry).is_symlink()


def test_keeps_lock_whose_owner_is_alive(tmp_path: Path) -> None:
    """A running browser owns its profile — pruning here would let a second
    Chromium scribble over a live profile, which is what the lock prevents."""
    profile = tmp_path / "chromium"
    live_socket = tmp_path / "SingletonSocket.live"
    live_socket.write_text("")
    _write_lock(profile, pid=os.getpid(), socket_path=live_socket)

    assert prune_stale_singleton_locks(profile) == []
    assert (profile / "SingletonLock").is_symlink()


@posix_only
def test_keeps_lock_owned_by_another_host(tmp_path: Path) -> None:
    """A pid from a different machine says nothing about this one — a profile on
    shared storage may be genuinely open elsewhere."""
    profile = tmp_path / "chromium"
    _write_lock(profile, pid=_dead_pid(), socket_path=None, host="some-other-host")

    assert prune_stale_singleton_locks(profile) == []
    assert (profile / "SingletonLock").is_symlink()


def test_no_lock_present_is_a_noop(tmp_path: Path) -> None:
    profile = tmp_path / "chromium"
    profile.mkdir(parents=True)

    assert prune_stale_singleton_locks(profile) == []


def test_missing_directory_is_a_noop(tmp_path: Path) -> None:
    assert prune_stale_singleton_locks(tmp_path / "never-created") == []


def test_unparsable_lock_target_is_left_alone(tmp_path: Path) -> None:
    """Only a lock we can positively read as a dead local pid is safe to remove."""
    profile = tmp_path / "chromium"
    profile.mkdir(parents=True)
    (profile / "SingletonLock").symlink_to("garbage-not-a-pid")

    assert prune_stale_singleton_locks(profile) == []
    assert (profile / "SingletonLock").is_symlink()


@posix_only
def test_prunes_when_only_the_socket_is_gone(tmp_path: Path) -> None:
    """The macOS case: the pid is dead AND the temp dir holding the socket was
    cleared, leaving a lock pointing at nothing."""
    profile = tmp_path / "chromium"
    _write_lock(profile, pid=_dead_pid(), socket_path=tmp_path / "cleared" / "SingletonSocket")

    assert sorted(prune_stale_singleton_locks(profile)) == sorted(_ENTRIES)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")
def test_windows_never_prunes(tmp_path: Path) -> None:
    """On Windows the probe would terminate the process rather than test it, so
    pruning must not run at all — Chromium doesn't write these entries there."""
    profile = tmp_path / "chromium"
    _write_lock(profile, pid=_dead_pid(), socket_path=None)

    assert prune_stale_singleton_locks(profile) == []
