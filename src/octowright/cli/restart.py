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

Note for AI agents: this restarts the *Octowright daemon*. If your symptom
is ``Transport closed`` on an MCP call, the daemon is likely fine — your
MCP client lost its stdio bridge and only restarting your agent (the MCP
client) will re-establish it. See SKILL.md "When something's wrong".
"""

from __future__ import annotations

import os
import signal
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


def _leader_pids_from_pgrep() -> list[int]:
    """Find ``octowright serve`` parent processes on the box.

    Used as a fallback when the lockfile is missing or stale. Matches the
    parent ``uv run octowright serve`` and the python child both — taskkill
    /the SIGTERM cascade will catch grandchildren.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", "octowright serve"],
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
            pids.append(int(line))
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
            _send_signal(pid, signal.SIGKILL)
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


def _wait_for_health(host: str, port: int, timeout: float) -> bool:
    """Poll ``/api/health`` until it answers 200, or ``timeout`` elapses.

    Uses ``httpx`` rather than ``urllib.request.urlopen`` so bandit's B310
    (file:// / custom-scheme risk on urlopen) doesn't fire — the scheme is
    fixed to http here, but B310 can't see that statically.
    """
    import httpx

    url = f"http://{host}:{port}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(_POLL_INTERVAL_S)
    return False


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

    Useful when an MCP transport is stuck or the daemon is wedged. Note that
    this does NOT fix ``Transport closed`` errors in your MCP client — those
    require restarting the client (Claude Code / Codex CLI), not the daemon.
    """
    stopped, killed = _stop_leader(timeout)
    if not keep_browsers:
        _reap_browsers()

    if no_start:
        click.echo(f"done (stopped={stopped} sigkilled={killed}; not starting a new daemon)")
        return

    _spawn_daemon(http_host, http_port)
    if _wait_for_health(http_host, http_port, timeout):
        click.echo(f"daemon healthy at http://{http_host}:{http_port}/")
    else:
        click.echo(
            f"WARNING: daemon did not become healthy within {timeout:.1f}s — check ``octowright serve`` logs",
            err=True,
        )
        ctx.exit(1)
