# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Find which process is *listening* on a TCP port, by the socket rather than by
parsing command lines.

``restart`` scopes its process sweep to a port read from each daemon's
``--http-port`` flag. That breaks for a split-brain daemon that bound the default
port and therefore carries no ``--http-port`` in its command line: restart can't
tell which port it holds, never targets it, and then fails to bind the spawn port
it is squatting on. Asking the OS who actually listens on the port sidesteps the
guesswork — the caller command-verifies the returned pid before signalling it.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable


def _pid_listening_on_port_posix(port: int) -> int | None:
    """Lowest pid with a LISTEN socket on ``port`` (POSIX, via ``lsof``)."""
    try:
        out = subprocess.run(  # nosec B603 B607
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # lsof missing or the probe failed — degrade to "unknown" rather than
        # crash restart; the caller just skips reclaiming the port.
        return None
    pids = [int(line) for line in out.stdout.split() if line.strip().isdigit()]
    return min(pids) if pids else None


def _pid_listening_on_port_windows(port: int) -> int | None:
    """Owning pid of a LISTENING socket on ``port`` (Windows, via PowerShell)."""
    script = (
        f"(Get-NetTCPConnection -State Listen -LocalPort {port} "
        "-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"
    )
    try:
        out = subprocess.run(  # nosec B603 B607
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip()
    return int(text) if text.isdigit() else None


def _pid_listening_on_port(port: int) -> int | None:
    if sys.platform == "win32":
        return _pid_listening_on_port_windows(port)
    return _pid_listening_on_port_posix(port)


def octowright_leader_on_port(
    port: int,
    list_processes: Callable[[], list[tuple[int, str]]],
) -> int | None:
    """Pid LISTENING on ``port`` iff it is an ``octowright serve`` process, else None.

    ``list_processes`` is injected (restart's ``ps``/PowerShell lister) so the
    command check uses the same process source as the rest of the sweep. The
    command is verified before returning, so a non-octowright holder of the port
    is never handed back for killing.
    """
    holder = _pid_listening_on_port(port)
    if holder is None:
        return None
    if any(pid == holder and "octowright serve" in cmd for pid, cmd in list_processes()):
        return holder
    return None
