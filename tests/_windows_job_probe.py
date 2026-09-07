# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Spawn a detached child from inside a Windows job, and report where it landed.

Run as a subprocess by ``tests/test_windows_job_detachment.py``. It exists as a
separate process because the scenario it models is *fatal to its own runner*:
the job is created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and this process
joins it, so closing the job kills everything still inside. Doing that in the
pytest process would end the test run.

Sequence::

    create job (KILL_ON_JOB_CLOSE [+ BREAKAWAY_OK])
    assign self to it
    daemonize._spawn_detached([...])     <- the real ladder under test
    report the child's pid and whether it is a member of the job
    block on stdin until the test says go
    exit  -> last job handle closes -> members are killed

``IsProcessInJob`` is the load-bearing measurement, not survival. Survival is
what an operator cares about, but it is observed *after* the fact and could be
explained by several things; membership answers the actual question --
did ``CREATE_BREAKAWAY_FROM_JOB`` take effect -- at the moment of the spawn,
which is the one thing ``_spawn_detached`` returning a pid never said.

Usage: ``python tests/_windows_job_probe.py <report.json> <breakaway_ok:0|1>``
"""

from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path

# winnt.h
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# How long the spawned child stays alive. Long enough that the test always
# decides its fate, short enough that a probe abandoned by a killed test run
# does not linger on a CI runner for the rest of the day.
_CHILD_LIFETIME_SECONDS = 300


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        # ULONG_PTR-sized fields: c_size_t matches on both 32- and 64-bit, and
        # getting the width wrong here silently misaligns LimitFlags' effect.
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32() -> ctypes.WinDLL:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def _create_job(k: ctypes.WinDLL, *, breakaway_ok: bool) -> wintypes.HANDLE:
    job = k.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = _ExtendedLimits()
    flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if breakaway_ok:
        flags |= _JOB_OBJECT_LIMIT_BREAKAWAY_OK
    limits.BasicLimitInformation.LimitFlags = flags
    ok = k.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(limits), ctypes.sizeof(limits))
    if not ok:
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return job


def _in_job(k: ctypes.WinDLL, handle: wintypes.HANDLE, job: wintypes.HANDLE | None) -> bool | None:
    """Whether *handle*'s process belongs to *job* (or to any job, if None)."""
    result = wintypes.BOOL()
    if not k.IsProcessInJob(handle, job, ctypes.byref(result)):
        return None
    return bool(result.value)


def main(argv: list[str]) -> int:
    report_path = Path(argv[1])
    breakaway_ok = argv[2] == "1"

    k = _kernel32()
    me = k.GetCurrentProcess()
    # Recorded, not asserted on: a CI runner may already have put this process
    # in a job of its own, and nested-job breakaway semantics are the most
    # likely reason for a surprising result here. A failure that cannot say
    # this is a failure someone has to reproduce by hand.
    already_in_a_job = _in_job(k, me, None)

    job = _create_job(k, breakaway_ok=breakaway_ok)
    if not k.AssignProcessToJobObject(job, me):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    # Imported here so an import failure is reported through this probe's own
    # stderr rather than at module load, where the test sees only an exit code.
    from octowright import daemonize

    # Keep the daemon log out of the user's real state dir; this spawns no
    # daemon, and a test must not append to a file an operator reads.
    daemonize._DAEMON_LOG = report_path.with_suffix(".daemonlog")

    child_pid = daemonize._spawn_detached([sys.executable, "-c", f"import time; time.sleep({_CHILD_LIFETIME_SECONDS})"])

    child = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, child_pid)
    member = _in_job(k, child, job) if child else None
    if child:
        k.CloseHandle(child)

    report_path.write_text(
        json.dumps(
            {
                "child_pid": child_pid,
                "child_in_job": member,
                "probe_was_already_in_a_job": already_in_a_job,
                "breakaway_ok": breakaway_ok,
            }
        ),
        encoding="utf-8",
    )
    # Hold the job open until the test has pinned a handle to the child;
    # otherwise the kill races the test's first observation and a recycled pid
    # could be measured instead.
    sys.stdin.read()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
