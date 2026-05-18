# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright restart`` — stop the running leader, sweep orphan browsers,
optionally start a fresh detached daemon.

Use case: an agent (Claude Code, Codex CLI, etc.) sees ``Transport closed``
from MCP tool calls and isn't sure whether the daemon is healthy. Manual
recovery is fiddly — pgrep, kill, wait for the port to release, spawn again,
mop up zombie browsers — and an agent that tries to script it tends to
accumulate orphans instead of replacing them (see the transcripts that
motivated this command). ``octowright restart`` does the full dance:

1. Find the leader via the singleton lockfile (with a ``pgrep`` fallback for
   a stale lockfile).
2. SIGTERM it. Wait up to ``--timeout`` seconds for graceful exit.
3. SIGKILL anything still alive.
4. Remove a stale lockfile if present.
5. Reap orphan Playwright browsers (skip with ``--keep-browsers``).
6. Spawn a fresh detached daemon (skip with ``--no-start``).
7. Probe ``/api/health`` until 200 OK, up to ``--timeout`` seconds.

Note for AI agents: this restarts the *Octowright daemon*. It intentionally
does not kill bare ``octowright serve`` follower processes owned by MCP
clients; those followers are the client's stdio transport.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import click

from octowright import singleton
from octowright.cli._root import cli
from octowright.defaults import HTTP_HOST, HTTP_PORT
from octowright.process_reaper import reap_orphan_browsers

# Poll interval while waiting for graceful shutdown or health-probe success.
_POLL_INTERVAL_S = 0.25

# Windows has no SIGKILL — TerminateProcess is invoked for any non-SIGTERM
# signum, so SIGTERM is the strongest available signal. Matches the
# escalation pattern in ``process_reaper.KILL_SIGNAL``.
_FORCE_KILL: int = getattr(signal, "SIGKILL", signal.SIGTERM)


def _resolve_octowright_entry() -> str:
    """Path to the installed ``octowright`` console script for this interpreter."""
    venv_bin = Path(sys.executable).parent / "octowright"
    if venv_bin.exists():
        return str(venv_bin)
    import shutil

    on_path = shutil.which("octowright")
    return on_path or str(venv_bin)


def _leader_pid_from_lock() -> int | None:
    info = singleton.read_lock()
    if info is None:
        return None
    return info.pid if singleton.pid_is_alive(info.pid) else None


def _looks_like_restart_target(command: str) -> bool:
    """Return True for daemon/launcher ``serve`` processes restart may kill.

    Bare ``octowright serve`` processes are usually MCP stdio followers. Killing
    them severs the connected client's transport, which is the failure mode
    ``restart`` is supposed to recover from. Detached daemon launchers always
    carry an explicit host/port, and daemon children carry ``--daemon-mode``.
    """
    if "octowright serve" not in command:
        return False
    return "--daemon-mode" in command or "--http-host" in command or "--http-port" in command


def _leader_pids_from_pgrep() -> list[int]:
    """Find daemon/launcher ``octowright serve`` pids when the lock is stale.

    Despite the historical name, this uses ``ps`` so we can inspect command
    lines and avoid killing bare follower transports attached to MCP clients.
    """
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_text, command = line.split(None, 1)
        except ValueError:
            continue
        if not _looks_like_restart_target(command):
            continue
        try:
            pids.append(int(pid_text))
        except ValueError:
            continue
    return pids


def _send_signal(pid: int, sig: int) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def _wait_for_pid_exit(pid: int, timeout: float) -> bool:
    """Return True if ``pid`` exited within ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not singleton.pid_is_alive(pid):
            return True
        time.sleep(_POLL_INTERVAL_S)
    return not singleton.pid_is_alive(pid)


def _port_is_free(host: str, port: int) -> bool:
    try:
        addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not addrinfos:
        return False
    checked = False
    seen: set[tuple[int, int, int, object]] = set()
    for family, socktype, proto, _canonname, sockaddr in addrinfos:
        key = (family, socktype, proto, sockaddr)
        if key in seen:
            continue
        seen.add(key)
        try:
            sock = socket.socket(family, socktype, proto)
        except OSError:
            continue
        try:
            sock.bind(sockaddr)
            checked = True
        except OSError:
            return False
        finally:
            sock.close()
    return checked


def _wait_for_port_free(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_free(host, port):
            return True
        time.sleep(_POLL_INTERVAL_S)
    return _port_is_free(host, port)


def _stop_leader(timeout: float) -> tuple[int, int]:
    """SIGTERM all known leader pids, escalate to SIGKILL on holdouts.

    Returns ``(stopped_count, kill9_count)``.
    """
    pids: set[int] = set()
    locked = _leader_pid_from_lock()
    if locked:
        pids.add(locked)
    pids.update(_leader_pids_from_pgrep())
    if not pids:
        click.echo("no running octowright daemon found")
        return 0, 0

    click.echo(f"stopping {len(pids)} octowright process(es): {sorted(pids)}")
    for pid in pids:
        _send_signal(pid, signal.SIGTERM)

    survivors = [pid for pid in pids if not _wait_for_pid_exit(pid, timeout)]
    if survivors:
        click.echo(f"  escalating to SIGKILL on {survivors}")
        for pid in survivors:
            _send_signal(pid, _FORCE_KILL)
            _wait_for_pid_exit(pid, 2.0)

    singleton.remove_lock()
    return len(pids) - len(survivors), len(survivors)


def _reap_browsers() -> None:
    summary = reap_orphan_browsers(scope="all")
    click.echo(
        f"orphan browsers: killed={len(summary['killed'])} "
        f"still_alive={len(summary['still_alive'])} "
        f"errors={len(summary['errors'])}"
    )


def _spawn_daemon(http_host: str, http_port: int) -> int:
    """Spawn a fresh detached daemon. Returns the launcher pid.

    Passes the requested host/port through so the health probe that follows
    is checking the same endpoint the daemon was asked to bind. Without this
    the daemon would default to ``defaults.HTTP_PORT`` and silently retry up
    if it was busy, leaving the probe target out of sync.
    """
    octowright = _resolve_octowright_entry()
    proc = subprocess.Popen(
        [octowright, "serve", "--http-host", http_host, "--http-port", str(http_port)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    click.echo(f"spawned octowright serve (launcher pid={proc.pid}) on {http_host}:{http_port}")
    return proc.pid


def _health_candidates(host: str, port: int) -> list[str]:
    """Health URLs to probe: requested endpoint first, lockfile endpoint next."""
    urls = [f"http://{host}:{port}/api/health"]
    info = singleton.read_lock()
    if info is not None and singleton.pid_is_alive(info.pid):
        urls.append(f"http://{info.http_host}:{info.http_port}/api/health")
    return list(dict.fromkeys(urls))


def _wait_for_health(host: str, port: int, timeout: float) -> str | None:
    """Poll ``/api/health`` until it answers 200, or ``timeout`` elapses.

    Uses ``httpx`` rather than ``urllib.request.urlopen`` so bandit's B310
    (file:// / custom-scheme risk on urlopen) doesn't fire — the scheme is
    fixed to http here, but B310 can't see that statically.

    Returns the dashboard base URL that actually answered. The daemon may bind
    to a higher port when the requested port is busy, so the lockfile endpoint
    is authoritative after spawn.
    """
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for url in _health_candidates(host, port):
            try:
                response = httpx.get(url, timeout=1.0)
                if response.status_code == 200:
                    return url.rsplit("/api/health", 1)[0] + "/"
            except httpx.HTTPError:
                pass
        time.sleep(_POLL_INTERVAL_S)
    return None


@cli.command()
@click.option(
    "--keep-browsers",
    is_flag=True,
    help="Don't sweep orphan Playwright browsers after stopping the daemon.",
)
@click.option(
    "--no-start",
    is_flag=True,
    help="Stop the daemon (and reap browsers) without spawning a fresh one.",
)
@click.option(
    "--timeout",
    type=float,
    default=10.0,
    show_default=True,
    help="Seconds to wait for graceful shutdown and health-probe success.",
)
@click.option(
    "--http-host",
    default=HTTP_HOST,
    show_default=True,
    help="Host to probe when verifying the new daemon is healthy.",
)
@click.option(
    "--http-port",
    type=int,
    default=HTTP_PORT,
    show_default=True,
    help="Port to probe when verifying the new daemon is healthy.",
)
@click.pass_context
def restart(
    ctx: click.Context,
    keep_browsers: bool,
    no_start: bool,
    timeout: float,
    http_host: str,
    http_port: int,
) -> None:
    """Stop the running octowright daemon, sweep orphans, start a fresh one.

    Useful when the daemon is wedged or needs a clean restart. The command
    preserves bare follower transports owned by MCP clients.
    """
    stopped, killed = _stop_leader(timeout)
    if not keep_browsers:
        _reap_browsers()

    if no_start:
        click.echo(f"done (stopped={stopped} sigkilled={killed}; not starting a new daemon)")
        return

    if not _wait_for_port_free(http_host, http_port, timeout):
        click.echo(
            f"WARNING: requested port {http_host}:{http_port} is still busy after {timeout:.1f}s; "
            "not starting a daemon on a fallback port",
            err=True,
        )
        ctx.exit(1)

    _spawn_daemon(http_host, http_port)
    healthy_url = _wait_for_health(http_host, http_port, timeout)
    if healthy_url is not None:
        click.echo(f"daemon healthy at {healthy_url}")
    else:
        click.echo(
            f"WARNING: daemon did not become healthy within {timeout:.1f}s — check ``octowright serve`` logs",
            err=True,
        )
        ctx.exit(1)
