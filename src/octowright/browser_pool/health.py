# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Compute a single health verdict from stability signals.

``octowright_status`` exposes raw counters (driver_restarts, recovery_failures,
…) but nobody reads them until something is already wrong. ``assess`` rolls them
into ``{status, reasons}`` so the first-touch status banner can proactively say
"degraded: shared driver restarted 3x" instead of burying the signal in numbers.
Pure function — the caller gathers the inputs and decides whether to log.
"""

from __future__ import annotations

import os
from typing import Any

# Thresholds at which a recurring failure stops being a blip and becomes a
# "something is structurally wrong" signal. Overridable for tuning.
CRITICAL_DRIVER_RESTARTS = int(os.environ.get("OCTOWRIGHT_HEALTH_CRITICAL_DRIVER_RESTARTS", "3"))
CRITICAL_RECOVERY_FAILURES = int(os.environ.get("OCTOWRIGHT_HEALTH_CRITICAL_RECOVERY_FAILURES", "5"))

_OK = "ok"
_DEGRADED = "degraded"
_CRITICAL = "critical"
_RANK = {_OK: 0, _DEGRADED: 1, _CRITICAL: 2}


def _worse(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


def assess(*, driver_restarts: int, recovery_failures: int, recovery_exhausted: int) -> dict[str, Any]:
    """Return ``{"status": ok|degraded|critical, "reasons": [...]}``.

    Each non-zero signal degrades health and adds a human-readable reason; the
    driver-restart and recovery-failure counts escalate to ``critical`` past
    their thresholds (a single SPOF death or one flaky renderer is degraded;
    repeated ones are critical).
    """
    status = _OK
    reasons: list[str] = []

    if driver_restarts > 0:
        reasons.append(f"shared Playwright driver restarted {driver_restarts}x (every browser was lost on each)")
        status = _worse(status, _DEGRADED)
    if recovery_failures > 0:
        reasons.append(f"{recovery_failures} renderer-crash auto-recoveries failed (browser process likely died)")
        status = _worse(status, _DEGRADED)
    if recovery_exhausted > 0:
        reasons.append(f"{recovery_exhausted} session(s) hit the crash-recovery cap — a crash loop")
        status = _worse(status, _DEGRADED)

    if driver_restarts >= CRITICAL_DRIVER_RESTARTS or recovery_failures >= CRITICAL_RECOVERY_FAILURES:
        status = _CRITICAL

    return {"status": status, "reasons": reasons}
