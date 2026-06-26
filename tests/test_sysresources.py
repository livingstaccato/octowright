# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-platform available-memory reading (octowright.sysresources).

The H4b memory governor refuses launches under low memory, so it MUST read
*available* memory correctly per platform — a naive sysconf "free" reading on
macOS reports most RAM as used (it's cache/purgeable) and would cause false
refusals. These tests pin the Linux MemAvailable parse, the macOS vm_stat
free+inactive+speculative+purgeable parse, and the platform dispatch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from octowright import sysresources

_MEMINFO = """\
MemTotal:       16384000 kB
MemFree:         1000000 kB
MemAvailable:    8000000 kB
Buffers:          200000 kB
Cached:          4000000 kB
"""

_VM_STAT = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              100000.
Pages active:                            500000.
Pages inactive:                          200000.
Pages speculative:                         5000.
Pages throttled:                              0.
Pages wired down:                        300000.
Pages purgeable:                          10000.
Pages stored in compressor:                   0.
"""


def test_linux_parse_uses_memavailable() -> None:
    # MemAvailable 8000000 kB → bytes.
    assert sysresources._linux_available_bytes(_MEMINFO) == 8000000 * 1024


def test_linux_parse_missing_memavailable_returns_none() -> None:
    assert sysresources._linux_available_bytes("MemTotal: 100 kB\nMemFree: 50 kB\n") is None


def test_macos_parse_sums_reclaimable_pages() -> None:
    # free+inactive+speculative+purgeable = 100000+200000+5000+10000 = 315000 pages.
    assert sysresources._macos_available_bytes(_VM_STAT) == 315000 * 16384


def test_macos_parse_no_page_size_returns_none() -> None:
    assert sysresources._macos_available_bytes("Pages free: 100.\n") is None


def test_macos_parse_no_known_keys_returns_none() -> None:
    text = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages wired down: 5.\n"
    assert sysresources._macos_available_bytes(text) is None


def test_read_proc_meminfo_reads_the_configured_path(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "meminfo"
    f.write_text(_MEMINFO)
    monkeypatch.setattr(sysresources, "_PROC_MEMINFO", f)
    assert "MemAvailable" in sysresources._read_proc_meminfo()


def test_run_vm_stat_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysresources.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=_VM_STAT))
    assert sysresources._run_vm_stat() == _VM_STAT


def test_run_vm_stat_returns_none_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysresources.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert sysresources._run_vm_stat() is None


def test_dispatch_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysresources.sys, "platform", "linux")
    monkeypatch.setattr(sysresources, "_read_proc_meminfo", lambda: _MEMINFO)
    assert sysresources.available_memory_bytes() == 8000000 * 1024


def test_dispatch_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysresources.sys, "platform", "darwin")
    monkeypatch.setattr(sysresources, "_run_vm_stat", lambda: _VM_STAT)
    assert sysresources.available_memory_bytes() == 315000 * 16384


def test_dispatch_macos_vm_stat_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysresources.sys, "platform", "darwin")
    monkeypatch.setattr(sysresources, "_run_vm_stat", lambda: None)
    assert sysresources.available_memory_bytes() is None


def test_dispatch_unknown_platform_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysresources.sys, "platform", "win32")
    assert sysresources.available_memory_bytes() is None


def test_dispatch_swallows_reader_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysresources.sys, "platform", "linux")

    def _boom() -> str:
        raise OSError("proc unreadable")

    monkeypatch.setattr(sysresources, "_read_proc_meminfo", _boom)
    assert sysresources.available_memory_bytes() is None


def test_available_memory_on_host_is_none_or_positive() -> None:
    # Exercises the real per-host reader; never raises, returns None or > 0.
    result = sysresources.available_memory_bytes()
    assert result is None or result > 0
