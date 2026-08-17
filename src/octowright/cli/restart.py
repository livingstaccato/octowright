# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright restart`` — stop the running leader, sweep browsers,
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
5. Reap Playwright browsers -- ``scope="all"``, so every browser on the box,
   protected or not (skip with ``--keep-browsers``).
6. Spawn a fresh detached daemon (skip with ``--no-start``).
7. Probe ``/api/health`` until 200 OK, up to ``--timeout`` seconds.

Note for AI agents: this restarts the *Octowright daemon*. It intentionally
does not kill bare ``octowright serve`` follower processes owned by MCP
clients; those followers are the client's stdio transport.
"""

from __future__ import annotations

import csv
import io
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import click

from octowright import singleton
from octowright.cli import port_owner
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


def _command_port(command: str) -> int | None:
    """The ``--http-port`` value in a serve command, or None if absent/unparsable."""
    tokens = command.split()
    for i, tok in enumerate(tokens):
        if tok == "--http-port" and i + 1 < len(tokens):
            try:
                return int(tokens[i + 1])
            except ValueError:
                return None
        if tok.startswith("--http-port="):
            try:
                return int(tok.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _restart_target_port() -> int:
    """The HTTP port this restart manages — the live lock's port, else the
    configured default. The process sweep is scoped to it so restart NEVER stops
    a daemon on a different port (an isolated/test daemon, or another project's)."""
    info = singleton.read_lock()
    if info is not None and singleton.pid_is_alive(info.pid):
        return info.http_port
    from octowright.defaults import HTTP_PORT

    return HTTP_PORT


def _looks_like_restart_target(command: str, target_port: int) -> bool:
    """Return True for a daemon/launcher ``serve`` process ON ``target_port`` that
    restart should stop.

    Bare ``octowright serve`` processes are MCP stdio followers — killing them
    severs the client's transport, the very failure restart recovers from, so
    they're excluded. Detached daemons always carry an explicit ``--http-port``,
    so matching that port is reliable AND keeps restart from cross-killing a
    daemon on another port (the isolation bug an isolated-lock restart used to
    hit). A daemon with no explicit port is left alone — the lockfile PID path
    still covers the one daemon restart is actually replacing.
    """
    if "octowright serve" not in command:
        return False
    if "--daemon-mode" not in command and "--http-host" not in command and "--http-port" not in command:
        return False
    return _command_port(command) == target_port


def _looks_like_follower(command: str) -> bool:
    """Return True for bare MCP-follower ``serve`` processes.

    Followers are the stdio transport for an MCP client session (Claude Code,
    Codex, etc.). They are NOT killed by default — killing them severs the
    client's connection. ``--kill-followers`` sweeps them for a full reset
    when sessions are already dead or the user explicitly wants a clean slate.
    """
    if "octowright serve" not in command:
        return False
    return "--daemon-mode" not in command and "--http-host" not in command and "--http-port" not in command


def _list_process_commands() -> list[tuple[int, str]]:
    """Return ``[(pid, command_line), ...]`` for every live process.

    Uses ``ps`` on POSIX and PowerShell ``Get-CimInstance`` on Windows
    (same approach as ``process_reaper``). Returns an empty list on any
    failure so callers degrade gracefully.
    """
    if sys.platform == "win32":
        return _list_process_commands_windows()
    return _list_process_commands_posix()


def _list_process_commands_posix() -> list[tuple[int, str]]:
    try:
        out = subprocess.run(  # nosec B603 B607
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    rows: list[tuple[int, str]] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_text, command = line.split(None, 1)
            rows.append((int(pid_text), command))
        except ValueError:
            continue
    return rows


def _list_process_commands_windows() -> list[tuple[int, str]]:
    # ``wmic`` is deprecated on recent Windows; ``Get-CimInstance`` is current.
    # Matches the pattern in ``process_reaper._list_processes_windows``.
    script = "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation"
    try:
        out = subprocess.run(  # nosec B603 B607
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    rows: list[tuple[int, str]] = []
    reader = csv.reader(io.StringIO(out.stdout))
    try:
        header = next(reader)
    except StopIteration:
        return rows
    try:
        pid_idx = header.index("ProcessId")
        cmd_idx = header.index("CommandLine")
    except ValueError:
        return rows
    for row in reader:
        if len(row) <= max(pid_idx, cmd_idx):
            continue
        try:
            rows.append((int(row[pid_idx]), row[cmd_idx] or ""))
        except ValueError:
            continue
    return rows


def _follower_pids() -> list[int]:
    """Return PIDs of all live bare follower ``octowright serve`` processes."""
    return [pid for pid, cmd in _list_process_commands() if _looks_like_follower(cmd)]


def _leader_pids_from_pgrep(target_port: int) -> list[int]:
    """Daemon/launcher ``octowright serve`` pids ON ``target_port`` (the port this
    restart manages). Despite the historical name this uses ``_list_process_commands``
    (``ps`` / PowerShell), so it can read command lines, skip bare followers, and —
    crucially — stay scoped to the target port instead of sweeping every daemon."""
    return [pid for pid, cmd in _list_process_commands() if _looks_like_restart_target(cmd, target_port)]


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
            # Match the daemon's bind options so this pre-flight check agrees
            # with what the new daemon can actually do: SO_REUSEADDR lets a
            # TIME_WAIT socket (from the daemon we just stopped) read as free,
            # so restart doesn't sit through the full TIME_WAIT timeout. An
            # actively-listening socket still blocks the bind, so a not-yet-dead
            # daemon is still correctly reported busy.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def _locked_pid_is_octowright(locked: int) -> bool:
    """Whether the lockfile-recorded leader pid is really an ``octowright serve``.

    The 0600 lockfile is same-user-writable and a recorded pid can be recycled by
    the OS to an unrelated process after the daemon dies. The port-scoped pgrep
    path already verifies command lines; the lockfile path did not, so a stale or
    poisoned lock could make restart SIGKILL a foreign / recycled pid. Verify the
    pid's command line before trusting it. If the pid isn't in the process list
    (a ps race), fall through to the pgrep path rather than killing blind.
    """
    return any(pid == locked and "octowright serve" in cmd for pid, cmd in _list_process_commands())


def _spawn_port_squatter(spawn_port: int | None, already: set[int]) -> int | None:
    """A split-brain octowright leader listening on the spawn port that isn't
    already in the kill set. Returned so the fresh daemon can bind. None when the
    port is free, held by a non-octowright process, or already targeted."""
    if spawn_port is None:
        return None
    squatter = port_owner.octowright_leader_on_port(spawn_port, _list_process_commands)
    if squatter is None or squatter in already:
        return None
    click.echo(f"reclaiming spawn port {spawn_port} from split-brain leader pid {squatter}")
    return squatter


def _collect_target_pids(kill_followers: bool, spawn_port: int | None = None) -> set[int]:
    """Return all PIDs that should be signalled.

    ``spawn_port`` is the port the fresh daemon will bind. If a *different*
    octowright leader is squatting on it (split-brain: the lockfile leader bumped
    to another port while this one holds the canonical port), it must be killed
    too or the spawn can't bind. It is found by the listening socket — its command
    line may lack ``--http-port``, so the port-scoped pgrep can't see it.
    """
    pids: set[int] = set()
    target_port = _restart_target_port()
    locked = _leader_pid_from_lock()
    if locked and _locked_pid_is_octowright(locked):
        pids.add(locked)
    elif locked:
        click.echo(
            f"lockfile leader pid {locked} is not an octowright daemon "
            "(stale lock or recycled pid) — not killing it directly",
            err=True,
        )
    pids.update(_leader_pids_from_pgrep(target_port))
    squatter = _spawn_port_squatter(spawn_port, pids)
    if squatter is not None:
        pids.add(squatter)
    if kill_followers:
        extra = [p for p in _follower_pids() if p not in pids]
        if extra:
            click.echo(f"killing {len(extra)} follower process(es): {sorted(extra)}")
        pids.update(extra)
    return pids


def _escalate_survivors(pids: set[int], timeout: float) -> list[int]:
    """Wait for each pid to exit; SIGKILL holdouts. Return pids still alive."""
    survivors = [pid for pid in pids if not _wait_for_pid_exit(pid, timeout)]
    if survivors:
        click.echo(f"  escalating to SIGKILL on {survivors}")
        for pid in survivors:
            _send_signal(pid, _FORCE_KILL)
            _wait_for_pid_exit(pid, 2.0)
    return survivors


def _stop_leader(timeout: float, *, kill_followers: bool = False, spawn_port: int | None = None) -> tuple[int, int]:
    """SIGTERM all known leader pids, escalate to SIGKILL on holdouts.

    When *kill_followers* is True, also sweeps bare MCP follower processes
    (``octowright serve`` without daemon flags) so stale sessions from dead
    clients don't accumulate. ``spawn_port`` lets the sweep also reclaim a
    split-brain leader squatting on the port the fresh daemon will bind.

    Returns ``(stopped_count, kill9_count)``.
    """
    pids = _collect_target_pids(kill_followers, spawn_port=spawn_port)
    if not pids:
        click.echo("no running octowright daemon found", err=True)
        return 0, 0
    click.echo(f"stopping {len(pids)} octowright process(es): {sorted(pids)}")
    for pid in pids:
        _send_signal(pid, signal.SIGTERM)
    survivors = _escalate_survivors(pids, timeout)
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
    # Resolved entrypoint path + literal flags, no shell.
    proc = subprocess.Popen(  # nosec B603
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
    help=(
        "Skip the browser sweep. Without this the restart kills every Playwright "
        "browser on the machine, protected ones included."
    ),
)
@click.option(
    "--no-start",
    is_flag=True,
    help="Stop the daemon (and reap browsers) without spawning a fresh one.",
)
@click.option(
    "--kill-followers",
    is_flag=True,
    help=(
        "Also kill bare MCP follower processes (octowright serve without daemon flags). "
        "Use for a full reset when prior client sessions are already dead. "
        "WARNING: severs any currently-connected MCP client transports."
    ),
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
    kill_followers: bool,
    timeout: float,
    http_host: str,
    http_port: int,
) -> None:
    """Stop the running octowright daemon, sweep browsers, start a fresh one.

    Useful when the daemon is wedged or needs a clean restart. The command
    preserves bare follower transports owned by MCP clients unless
    --kill-followers is passed (full reset).

    DESTRUCTIVE: the browser sweep kills every Playwright browser on this
    machine, not just leftovers from the dead daemon, and a protected browser
    is NOT spared -- the sweep signals raw pids, a layer that never sees the
    pool's protection flag. (protected only holds against cleanup and
    close_strays.) Pass --keep-browsers to leave browsers running.
    """
    # When we're going to spawn, also reclaim the spawn port from a split-brain
    # leader squatting on it (otherwise the bind below fails and nothing starts).
    spawn_port = None if no_start else http_port
    stopped, killed = _stop_leader(timeout, kill_followers=kill_followers, spawn_port=spawn_port)
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

    launcher_pid = _spawn_daemon(http_host, http_port)
    healthy_url = _wait_for_health(http_host, http_port, timeout)
    if healthy_url is not None:
        if stopped == 0 and killed == 0:
            # Nothing was running before — be explicit so an agent invoking
            # restart as a recovery action can see that no prior daemon was
            # found and a fresh one was started.
            click.echo(f"no prior daemon; started new one at PID {launcher_pid}")
        else:
            click.echo(f"restarted daemon (stopped={stopped} sigkilled={killed}; new launcher PID {launcher_pid})")
        click.echo(f"daemon healthy at {healthy_url}")
    else:
        click.echo(
            f"WARNING: daemon did not become healthy within {timeout:.1f}s — check ``octowright serve`` logs",
            err=True,
        )
        ctx.exit(1)
