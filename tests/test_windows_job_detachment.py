# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Does a spawned daemon actually outlive a Windows job teardown?

``daemonize._detach_candidates`` asks for ``CREATE_BREAKAWAY_FROM_JOB`` and
falls back without it, because a CI runner that tears its step down with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` kills every process still in the job no
matter how detached its console is. That ladder shipped, and a Windows leg went
green afterwards -- but green only proved the daemon *started*. Nothing had ever
torn a job down and looked, so the claim the flag exists to support was
unverified, and AGENTS.md says so in as many words.

These tests tear one down. Both arms matter and they assert opposite outcomes:

* a job that permits breakaway -- the child must escape and survive;
* a job that forbids it -- the child must NOT survive.

The second is not a negative for symmetry's sake. It pins the documented honest
limit ("a job that forbids breakaway still takes the daemon down with the step;
nothing a child process can do changes that"), so a later change that claims to
have solved that case has to prove it here rather than in a comment.

The scenario is fatal to whoever runs it, so the job lives in a helper process
(``tests/_windows_job_probe.py``); see its docstring for the sequence.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Job objects are a Windows concept; POSIX detaches with setsid and has one candidate",
)

_PROBE = Path(__file__).parent / "_windows_job_probe.py"

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259

# The job's kill is asynchronous with the handle close. Generous, and only ever
# waited out in full when the assertion is about to fail anyway.
_KILL_SETTLE_SECONDS = 15.0
_POLL_SECONDS = 0.1


def _kernel32() -> Any:
    """``ctypes.wintypes`` is imported lazily: it is Windows-only, and this
    module is *collected* on every platform even though every test in it skips."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    return kernel32


def _still_running(handle: Any) -> bool:
    from ctypes import wintypes

    kernel32 = _kernel32()
    code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return False
    return code.value == _STILL_ACTIVE


def _open_pinned(pid: int) -> Any:
    """A handle to *pid*, held so the pid cannot be recycled under us.

    Without it, "is that pid alive" is a question about whatever process holds
    the number by the time it is asked -- and the whole point of this test is
    that the process it names is about to be killed.
    """
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        raise AssertionError(f"could not open the spawned child (pid {pid}); it died before the job closed")
    return handle


def _run_probe(tmp_path: Path, *, breakaway_ok: bool) -> tuple[dict[str, Any], Any]:
    """Run the probe up to the point where it holds the job open.

    Returns its report and a pinned handle to the child it spawned. The probe
    is still alive on return; the caller closes its stdin to release it.
    """
    report = tmp_path / f"probe-{int(breakaway_ok)}.json"
    proc = subprocess.Popen(  # nosec B603
        [sys.executable, str(_PROBE), str(report), "1" if breakaway_ok else "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if report.exists():
            break
        if proc.poll() is not None:
            _, err = proc.communicate()
            raise AssertionError(f"probe exited before reporting (rc={proc.returncode}):\n{err}")
        time.sleep(_POLL_SECONDS)
    else:
        proc.kill()
        raise AssertionError("probe never wrote its report")

    payload = json.loads(report.read_text(encoding="utf-8"))
    handle = _open_pinned(payload["child_pid"])
    payload["_proc"] = proc
    return payload, handle


def _close_the_job(payload: dict[str, Any]) -> None:
    """Release the probe so it exits, closing the last handle to its job."""
    proc = payload["_proc"]
    proc.stdin.close()
    proc.wait(timeout=60)


def _await_death(handle: Any) -> bool:
    deadline = time.monotonic() + _KILL_SETTLE_SECONDS
    while time.monotonic() < deadline:
        if not _still_running(handle):
            return True
        time.sleep(_POLL_SECONDS)
    return False


def _kill(pid: int) -> None:
    subprocess.run(  # nosec B603 B607
        ["taskkill", "/F", "/PID", str(pid)],
        capture_output=True,
        check=False,
    )


def test_a_detached_daemon_survives_a_job_that_permits_breakaway(tmp_path: Path) -> None:
    payload, handle = _run_probe(tmp_path, breakaway_ok=True)
    try:
        assert payload["child_in_job"] is False, (
            "CREATE_BREAKAWAY_FROM_JOB did not take effect even though the job set "
            f"BREAKAWAY_OK; probe report: {payload!r}"
        )
        _close_the_job(payload)
        # Give the kill every chance to land before claiming it did not: a pass
        # here must mean "survived", not "we looked too early".
        time.sleep(1.0)
        assert _still_running(handle), (
            f"the spawned child died with the job it had broken away from; probe report: {payload!r}"
        )
    finally:
        _kill(payload["child_pid"])


def test_a_job_that_forbids_breakaway_still_takes_the_daemon_down(tmp_path: Path) -> None:
    """The documented limit, pinned so a future claim to have fixed it must prove it."""
    payload, handle = _run_probe(tmp_path, breakaway_ok=False)
    try:
        assert payload["child_in_job"] is True, (
            "the child escaped a job that never set BREAKAWAY_OK, which should be "
            f"impossible; probe report: {payload!r}"
        )
        _close_the_job(payload)
        assert _await_death(handle), f"the child outlived a job with KILL_ON_JOB_CLOSE; probe report: {payload!r}"
    finally:
        _kill(payload["child_pid"])
