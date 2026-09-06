# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A pull surface for launches refused by an input guard.

``BrowserPool.launch`` deliberately records nothing in ``engine_health`` when a
request is refused: no engine was asked to do anything, so a refusal says
nothing about whether one works (issue #214). That is correct, and it leaves an
operator with less than they had. ``octowright_launch_refused_total`` covers it
for anyone exporting telemetry, but metrics are a **noop unless
``PROVIDE_METRICS_ENABLED`` is set**, which is off by default -- so on an
ordinary deployment a client regression spamming invalid requests shows a
perfectly healthy daemon and no evidence at all. The same reasoning put
unresponsive targets into ``incidents``.

They are NOT put into ``incidents``, though, and that is the load-bearing
decision here. That ring is 25 entries **shared across every category**, and
its own docstring already notes that a repeatedly-firing category evicts the
others. A refusal flood is the highest-frequency event the daemon can see, so
recording each one there would push out precisely the renderer-crash and
driver records the ring exists for -- destroying a scarce diagnostic to serve a
cheaper one. This keeps aggregates instead: bounded by construction, with no
eviction policy to get wrong.

**What is deliberately not kept: the offending value.** The refused URL, path
or header is caller-supplied, and keeping it would undo the rule the rest of
this change enforces -- ``engine_health`` keeps exception class names and never
messages, and ``_metrics.launch_span`` stops the message reaching the OTLP
backend, both because a refusal message reliably carries a filesystem path or a
profile name. An operator learns that requests are being refused and which
guard refused them; *which* URL is the client's to report, and the caller
already has it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: Distinct guard keys retained before the rest are bucketed. The key is
#: derived from CODE (the module that raised), not from caller input, so it is
#: already bounded by the size of the tree -- this is the same belt-and-braces
#: the ``kind`` clamp applies, so a never-evicted dict echoed into every
#: ``octowright_status()`` cannot grow with a future refactor.
GUARD_KEY_CAP = 16
OTHER_GUARD_KEY = "other"
UNKNOWN_GUARD_KEY = "unknown"

_PACKAGE_PREFIX = "octowright."


def guard_of(exc: BaseException) -> str:
    """Name the guard that refused, as the module it was raised from.

    Read from the traceback's LAST frame rather than from the exception, so no
    guard has to remember to tag itself -- the enforcement problem this change
    already hit once, where a hand-maintained list of guards looked like a
    mechanism and was documentation. A guard added tomorrow is attributed
    correctly without touching it.

    The value is a module path (``session.core_page_mixin``,
    ``browser_pool.options``), which is code, never caller data -- so it can be
    a dict key safely, and stays legible when a function inside the guard is
    renamed.
    """
    tb = exc.__traceback__
    if tb is None:
        return UNKNOWN_GUARD_KEY
    while tb.tb_next is not None:
        tb = tb.tb_next
    module = tb.tb_frame.f_globals.get("__name__")
    if not isinstance(module, str) or not module:
        return UNKNOWN_GUARD_KEY
    return module.removeprefix(_PACKAGE_PREFIX)


class RefusalTracker:
    """Per-pool aggregate of refused launches. Never holds a caller value."""

    def __init__(self) -> None:
        self._by_guard: dict[str, int] = {}
        self._total = 0
        self._last_at: str | None = None

    def record(self, exc: BaseException) -> None:
        guard = guard_of(exc)
        if guard not in self._by_guard and len(self._by_guard) >= GUARD_KEY_CAP:
            guard = OTHER_GUARD_KEY
        self._by_guard[guard] = self._by_guard.get(guard, 0) + 1
        self._total += 1
        self._last_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def snapshot(self) -> dict[str, Any]:
        """Aggregates, always present -- including when nothing was refused.

        Deliberately unlike ``engine_health()``, which is absent for a kind
        never launched because "no data" and "fine" are different answers
        there. Here they are the same answer: ``total: 0`` means no request was
        refused, which is a complete and correct report rather than silence.
        """
        return {"total": self._total, "by_guard": dict(self._by_guard), "last_at": self._last_at}
