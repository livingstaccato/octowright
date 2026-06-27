# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Launch admission control: the browser cap + memory-pressure floor.

These live in the pool layer (not the MCP-tool wrapper) so **every** user-facing
launch path inherits them — including `scenario_start`, which calls
`pool.spawn_roster` directly and would otherwise bypass a tool-only check and let
a big scenario OOM the shared host. `roster.spawn_roster` is the single chokepoint
both the `browser_spawn_roster` tool and `scenario_start` route through; the
single-launch tools call `enforce_cap`/`enforce_memory` via thin shims in
`server/browser/lifecycle`.

Internal relaunch / handoff / crash-recovery call `pool.launch` directly and are
deliberately NOT admission-controlled here — they recover an *existing* session
rather than adding a new one, and must not be refused when the pool is at cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from octowright import defaults as _defaults
from octowright import sysresources as _sysresources
from octowright.browser_pool._metrics import LAUNCH_REFUSED
from octowright.browser_pool.errors import BrowserCapExceededError, MemoryPressureError

if TYPE_CHECKING:
    from octowright.browser_pool.pool import BrowserPool


def enforce_cap(pool: BrowserPool, *, adding: int) -> None:
    """Refuse a launch that would exceed ``OCTOWRIGHT_MAX_BROWSERS``.

    Read through the ``defaults`` module (not a bound import) so the cap honours
    a runtime/monkeypatched change. Off (None) → no-op.
    """
    cap = _defaults.MAX_BROWSERS
    if cap is None:
        return
    active = pool.active_count()
    if active + adding > cap:
        LAUNCH_REFUSED.add(1, attributes={"reason": "cap"})
        raise BrowserCapExceededError(
            f"browser cap reached: {active} live + {adding} requested would exceed "
            f"OCTOWRIGHT_MAX_BROWSERS={cap}. Close browsers (browser_close / "
            f"browser_close_all) or raise OCTOWRIGHT_MAX_BROWSERS. This cap is shared "
            f"across every MCP client connected to this daemon."
        )


def enforce_memory(*, adding: int) -> None:
    """Refuse a launch while available memory is below
    ``OCTOWRIGHT_MIN_FREE_MEMORY_MB``. Off (None floor) → no-op, and no memory
    read at all (the read is only paid when the guard is configured). An
    unreadable available value (None) never refuses — "unknown" must not wedge
    launches.
    """
    floor = _sysresources.MIN_FREE_MEMORY_BYTES
    if floor is None:
        return
    available = _sysresources.available_memory_bytes()
    if available is None:
        return
    if available < floor:
        LAUNCH_REFUSED.add(1, attributes={"reason": "memory"})
        mb = 1024 * 1024
        raise MemoryPressureError(
            f"refusing to launch {adding} browser(s): available memory "
            f"{available // mb}MB is below the OCTOWRIGHT_MIN_FREE_MEMORY_MB floor "
            f"({floor // mb}MB). Close browsers (browser_close / browser_close_all) or "
            f"free memory; set OCTOWRIGHT_MIN_FREE_MEMORY_MB=off to disable this guard."
        )


def enforce_launch_limits(pool: BrowserPool, *, adding: int) -> None:
    """The combined admission gate: cap first, then memory floor."""
    enforce_cap(pool, adding=adding)
    enforce_memory(adding=adding)
