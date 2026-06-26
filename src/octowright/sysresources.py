# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-platform *available* (reclaimable) physical memory reading.

The memory-pressure launch governor (see ``server/browser/lifecycle``) refuses
new browsers when free memory is low, to head off the OOM → renderer-crash
cascade. Getting "available" right per platform matters: the macOS sysconf
``SC_AVPHYS_PAGES`` "free" count reports most RAM as used — it's cache, inactive,
and purgeable pages the kernel hands back on demand — so a naive threshold would
refuse launches on a perfectly healthy Mac. We therefore read:

- **Linux**: ``/proc/meminfo`` ``MemAvailable`` (the kernel's own estimate of
  what's allocatable without swapping).
- **macOS**: ``vm_stat`` free + inactive + speculative + purgeable pages, which
  together approximate reclaimable memory.

Any other platform, or any read error, returns ``None`` — the governor treats
``None`` as "unknown" and never refuses on it, so a reading failure can't wedge
launches.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 - fixed-argv vm_stat read, no shell, no user input
import sys
from pathlib import Path

# Module-level so tests can repoint it without monkeypatching pathlib.
_PROC_MEMINFO = Path("/proc/meminfo")


def parse_min_free_memory_mb(raw: str | None) -> int | None:
    """Parse OCTOWRIGHT_MIN_FREE_MEMORY_MB into a byte floor, or None (off).

    A positive number is the floor in MB; ``""`` / ``0`` / ``off`` / ``never`` /
    ``none`` / ``disabled`` / unparsable disable the governor."""
    text = (raw or "").strip().lower()
    if text in ("", "0", "off", "never", "none", "disabled"):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value * 1024 * 1024) if value > 0 else None


# Memory-pressure launch governor (H4b). OFF by default: a Mac reports most RAM
# as "used" (cache/purgeable), so a default floor would false-refuse launches.
# When set, the user-facing launch tools refuse while available_memory_bytes()
# is below it — heading off the low-memory → renderer-crash cascade. Read here
# (not defaults.py, which is at its LOC ceiling), mirroring incidents/health.
MIN_FREE_MEMORY_BYTES: int | None = parse_min_free_memory_mb(os.environ.get("OCTOWRIGHT_MIN_FREE_MEMORY_MB"))

# vm_stat page buckets that count as reclaimable/available.
_MACOS_RECLAIMABLE_KEYS = ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")


def _linux_available_bytes(meminfo_text: str) -> int | None:
    """Parse ``MemAvailable`` (kB) out of ``/proc/meminfo`` text → bytes."""
    for line in meminfo_text.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


def _macos_available_bytes(vm_stat_text: str) -> int | None:
    """Sum the reclaimable ``vm_stat`` page buckets times page size, in bytes."""
    page_match = re.search(r"page size of (\d+) bytes", vm_stat_text)
    if page_match is None:
        return None
    page_size = int(page_match.group(1))

    values: dict[str, int] = {}
    for line in vm_stat_text.splitlines():
        key, sep, raw = line.partition(":")
        cleaned = raw.strip().rstrip(".")
        if sep and cleaned.isdigit():
            values[key.strip()] = int(cleaned)

    present = [values[key] for key in _MACOS_RECLAIMABLE_KEYS if key in values]
    return sum(present) * page_size if present else None


def _read_proc_meminfo() -> str:
    return _PROC_MEMINFO.read_text()


def _run_vm_stat() -> str | None:
    result = subprocess.run(  # nosec B603 B607 - fixed argv, absolute path, no shell
        ["/usr/bin/vm_stat"],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _parse_ps_rss_kb(ps_output: str) -> int:
    """Sum the per-PID RSS (KB) values printed by ``ps -o rss=``."""
    total = 0
    for line in ps_output.splitlines():
        value = line.strip()
        if value.isdigit():
            total += int(value)
    return total


def process_rss_bytes(pids: list[int]) -> int:
    """Summed resident-set size (bytes) of ``pids`` via ``ps`` (no psutil dep), or
    0 when the list is empty or ``ps`` can't be read. Never raises — a sampling
    failure must not crash the housekeeping loop that calls it."""
    if not pids:
        return 0
    try:
        result = subprocess.run(  # nosec B603 B607 - absolute path, fixed flags, pid list only
            ["/bin/ps", "-o", "rss=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode != 0:
            return 0
        return _parse_ps_rss_kb(result.stdout) * 1024
    except Exception:
        return 0


def available_memory_bytes() -> int | None:
    """Best-effort available physical memory in bytes, or ``None`` when it can't
    be determined (unsupported platform, read error). Never raises."""
    try:
        if sys.platform.startswith("linux"):
            return _linux_available_bytes(_read_proc_meminfo())
        if sys.platform == "darwin":
            out = _run_vm_stat()
            return _macos_available_bytes(out) if out is not None else None
    except Exception:
        return None
    return None
