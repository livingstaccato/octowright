# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Spawn a detached background leader so the leader survives parent close.

Why this exists: an MCP client launches ``octowright serve``
as a stdio child. When the client closes, it sends SIGTERM and (after a
brief grace) SIGKILL. SIGTERM we can catch (see ``cli.serve``); SIGKILL we
can't. Browsers die with the leader.

The fix: when a ``serve`` invocation decides it should become the leader,
it instead **forks a fully-detached background process** that becomes the
real leader. The original client-launched process becomes a follower
bridging to the daemon. Closing the MCP client kills the bridge but the
daemon — detached from the parent's process tree, with stdin/out/err
pointed at ``/dev/null`` — is unaffected.

The daemon is invoked with ``--daemon-mode``, which tells it to skip the
lock check (it knows it's the leader) and arm the idle watchdog
immediately (so an unused daemon exits after the grace period).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any

import octowright.singleton as _sn
from octowright.config_paths import user_state_dir

# Daemon stderr goes here so we have something to investigate when the daemon
# misbehaves. Rotated by file size on each spawn (truncated above 1 MB) to
# avoid unbounded growth on dev machines.
_DAEMON_LOG = user_state_dir() / "logs" / "octowright-daemon.log"
_DAEMON_LOG_MAX_BYTES = 1_000_000
# Lines of daemon stderr worth quoting back when a spawn fails. The log is the
# only record of WHY (the caller's own stderr holds the follower's output, not
# the daemon's), so a failure that doesn't surface this is undiagnosable from
# the outside -- the exact dead end a CI runner hits.
_DAEMON_LOG_TAIL_LINES = 20

# How long to wait for a spawned daemon to bind and answer HTTP. The default
# suits a warm dev machine; a cold container running ``uv run octowright serve``
# routinely needs longer, and exceeding it silently degrades to fragile inline
# mode. ``defaults.py`` is at its LOC ceiling, so the knob lives here (matching
# how ``incidents``/``health`` keep their own OCTOWRIGHT_* vars).
DAEMON_READY_TIMEOUT_ENV = "OCTOWRIGHT_DAEMON_READY_TIMEOUT"
DAEMON_READY_TIMEOUT_SECONDS = 10.0

# Windows process-creation flags (winbase.h). Named here rather than imported
# from ``subprocess`` because those attributes only exist on Windows builds.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def daemon_ready_timeout() -> float:
    """Seconds to wait for a spawned daemon, from the environment."""
    raw = os.environ.get(DAEMON_READY_TIMEOUT_ENV)
    if raw is None:
        return DAEMON_READY_TIMEOUT_SECONDS
    try:
        parsed = float(raw.strip())
    except ValueError:
        return DAEMON_READY_TIMEOUT_SECONDS
    return parsed if parsed > 0 else DAEMON_READY_TIMEOUT_SECONDS


def daemon_log_path() -> Path:
    """Where the detached daemon's stderr lands."""
    return _DAEMON_LOG


def daemon_log_tail(max_lines: int = _DAEMON_LOG_TAIL_LINES) -> str:
    """Last few lines of the daemon log, or a note saying why there are none.

    Callers print this when a spawn fails, so it must never raise: a missing
    or unreadable log is itself the diagnostic.
    """
    try:
        lines = _DAEMON_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return f"(no daemon log at {_DAEMON_LOG}; the daemon may never have started)"
    except OSError as exc:
        return f"(daemon log at {_DAEMON_LOG} unreadable: {exc})"
    tail = [line for line in lines if line.strip()][-max_lines:]
    if not tail:
        return f"(daemon log at {_DAEMON_LOG} is empty)"
    return "\n".join(tail)


def _detach_kwargs() -> dict[str, Any]:
    """Popen kwargs that put the daemon outside the parent's lifecycle.

    ``start_new_session`` is POSIX-only -- CPython accepts it on Windows and
    silently does nothing, so the "detached" daemon stayed inside the launching
    console's process tree and died with it. On a CI runner that tree is a job
    object the step tears down, which is why a Windows leg saw the daemon
    vanish and then fall back to inline mode.
    """
    if sys.platform == "win32":
        return {"creationflags": _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _resolve_daemon_entrypoint() -> list[str]:
    """Resolve the argv prefix that re-launches ``octowright serve``.

    ``sys.argv[0]`` is unreliable for re-launching the daemon: when the parent
    was started via ``python -m octowright``, ``sys.argv[0]`` is a module path
    that isn't directly executable, and various wrappers (pipx, uv tool) can
    also leave it in shapes that won't round-trip through ``Popen``. We prefer
    the installed console script, then fall back to ``python -m octowright``,
    and only as a last resort use the (possibly broken) ``sys.argv[0]``.
    """
    on_path = shutil.which("octowright")
    if on_path:
        return [on_path]
    if sys.executable:
        return [sys.executable, "-m", "octowright"]
    return [sys.argv[0]]


def _open_daemon_log() -> IO[bytes]:
    """Open the private daemon-stderr log, repairing legacy permissions."""
    _DAEMON_LOG.parent.mkdir(parents=True, exist_ok=True)
    if _DAEMON_LOG.exists() and _DAEMON_LOG.stat().st_size > _DAEMON_LOG_MAX_BYTES:
        _DAEMON_LOG.unlink()
    fd = os.open(_DAEMON_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        # ``mode`` only applies to a newly created file. Tighten pre-existing
        # logs too before the daemon can append potentially sensitive output.
        # Prefer the open descriptor to avoid a path race. Keep a fallback for
        # platforms/filesystems without useful fchmod semantics; permission
        # repair remains best-effort and cannot prevent daemon startup.
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            try:
                os.chmod(_DAEMON_LOG, 0o600)
            except OSError:
                pass
        return os.fdopen(fd, "ab")
    except BaseException:
        os.close(fd)
        raise


def spawn_daemon(
    *,
    http_host: str | None,
    http_port: int | None,
    idle_grace: float | None,
    keep_alive: bool = False,
) -> int:
    """Spawn a fully detached background ``octowright serve --daemon-mode`` process.

    Returns the spawned PID. The process is detached from the parent's
    lifecycle (a new session on POSIX, DETACHED_PROCESS on Windows -- see
    ``_detach_kwargs``) with stdin/stdout pointed at /dev/null, so nothing
    about the parent's lifecycle can reach it.

    ``keep_alive``/``idle_grace`` are forwarded to the daemon's argv so the
    follower's choice actually reaches the process that owns the watchdog (the
    detached daemon, not the follower). Without forwarding ``--keep-alive`` the
    flag was silently dropped and the daemon kept its own default.
    """
    args: list[str] = [*_resolve_daemon_entrypoint(), "serve", "--daemon-mode"]
    if http_host:
        args.extend(["--http-host", http_host])
    if http_port is not None:
        args.extend(["--http-port", str(http_port)])
    if idle_grace is not None:
        args.extend(["--idle-grace", str(idle_grace)])
    if keep_alive:
        args.append("--keep-alive")

    # Fixed argv (sys.argv[0] + flags); no shell. Daemon spawn for octowright serve.
    proc = subprocess.Popen(  # nosec B603
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=_open_daemon_log(),
        close_fds=True,
        env=os.environ.copy(),
        **_detach_kwargs(),
    )
    return proc.pid


async def wait_for_daemon(timeout: float | None = None, poll_seconds: float = 0.2) -> _sn.LeaderInfo | None:
    """Poll the lockfile + HTTP probe until the daemon is ready, or give up.

    Returns the leader info on success, or None if the daemon failed to come
    up within ``timeout`` seconds. The caller can then fall back to running
    the leader inline (the legacy non-daemonized path).

    ``timeout`` defaults to :func:`daemon_ready_timeout`, so a cold container
    can raise it via ``OCTOWRIGHT_DAEMON_READY_TIMEOUT`` or ``--ready-timeout``
    instead of silently degrading to inline mode.
    """
    deadline = time.monotonic() + (daemon_ready_timeout() if timeout is None else timeout)
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_seconds)
        info = _sn.read_lock()
        if info is None or _sn.is_stale(info):
            continue
        if await _sn.probe_http_alive(info):
            return info
    return None
