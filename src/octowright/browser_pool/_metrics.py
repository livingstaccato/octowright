# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""OTel metric instruments for browser_pool/pool.py.

Lives outside ``pool.py`` so the LOC ceiling there isn't inflated by
instrument declarations. Noop when metrics are disabled.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from octowright._tracing import counter, histogram, record_exception, span

LAUNCHED = counter(
    "octowright_browser_launched_total",
    description="Browsers launched",
)
LAUNCH_FAILED = counter(
    "octowright_browser_launch_failed_total",
    description="Failed browser launches",
)
LAUNCH_DURATION = histogram(
    "octowright_browser_launch_duration_seconds",
    description="Time from pool.launch() entry to registered session",
    unit="s",
)


@asynccontextmanager
async def launch_span(kind_hint: str) -> AsyncIterator[Any]:
    """Wrap pool.launch() with span + LAUNCH_FAILED counter on exception."""
    with span("octowright.browser.launch", kind=kind_hint) as sp:
        try:
            yield sp
        except BaseException as exc:
            record_exception(sp, exc)
            LAUNCH_FAILED.add(1, attributes={"kind": kind_hint, "error": type(exc).__name__})
            raise
