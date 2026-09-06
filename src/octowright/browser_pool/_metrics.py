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
from octowright.request_errors import InvalidRequestError

LAUNCHED = counter(
    "octowright_browser_launched_total",
    description="Browsers launched",
)
# Machinery-only: a launch an engine was actually asked to perform and did not.
# A request refused by an input guard is counted by LAUNCH_REFUSED instead --
# see launch_span.
LAUNCH_FAILED = counter(
    "octowright_browser_launch_failed_total",
    description="Browser launches an engine attempted and failed",
)
# Refused launches by reason (noop unless telemetry is on). `cap`/`memory` mean
# the pool is under capacity pressure; `invalid_request` means callers are
# sending requests the input guards reject, which is a client-side regression
# rather than a machine problem. Kept separate from LAUNCH_FAILED for that
# reason, and bounded to those three values.
LAUNCH_REFUSED = counter(
    "octowright_launch_refused_total",
    description="Launches refused (reason=cap|memory|invalid_request)",
)
LAUNCH_DURATION = histogram(
    "octowright_browser_launch_duration_seconds",
    description="Time from pool.launch() entry to registered session",
    unit="s",
)


@asynccontextmanager
async def launch_span(kind_hint: str) -> AsyncIterator[Any]:
    """Wrap pool.launch() with span + LAUNCH_FAILED counter on exception.

    An ``InvalidRequestError`` is MOVED to ``LAUNCH_REFUSED``, not dropped, for
    the reason ``BrowserPool.launch`` keeps it out of engine health: it is a
    rejection of the caller's own request, so counting it as
    ``{kind="chromium", error="ValueError"}`` tells an operator alerting on
    per-engine launch failures that Chromium is failing when nothing ever asked
    it to launch. Silence would be the same mistake mirrored: a client
    regression spamming invalid URLs, headers or paths fails every launch from
    the caller's side while a machinery-only counter stays flat, so a
    success-rate dashboard reports healthy traffic through a rejection flood.
    ``reason="invalid_request"`` keeps that visible on a bounded label.

    The exception is still recorded on the SPAN -- a trace is a record of what
    happened to one request, where a refusal belongs, rather than a per-engine
    health signal that a refusal can only corrupt.
    """
    with span("octowright.browser.launch", kind=kind_hint) as sp:
        try:
            yield sp
        except BaseException as exc:
            record_exception(sp, exc)
            if isinstance(exc, InvalidRequestError):
                LAUNCH_REFUSED.add(1, attributes={"reason": "invalid_request"})
            else:
                LAUNCH_FAILED.add(1, attributes={"kind": kind_hint, "error": type(exc).__name__})
            raise
