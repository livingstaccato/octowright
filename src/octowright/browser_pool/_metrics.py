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

from octowright._tracing import counter, histogram, record_exception, set_attrs, span
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
    regression spamming invalid URLs or paths fails every launch from the
    caller's side while a machinery-only counter stays flat, so a success-rate
    dashboard reports healthy traffic through a rejection flood.
    ``reason="invalid_request"`` keeps that visible on a bounded label.

    Scope, precisely: this counts refusals raised INSIDE ``pool.launch``.
    Header and header-URL-pattern validation also runs in
    ``LaunchOptions.to_pool_kwargs``, which ``server/browser/lifecycle`` calls
    to BUILD the launch arguments -- before ``pool.launch`` is entered -- so a
    malformed ``extra_http_headers`` is refused earlier and is not counted
    here. That is a gap in the counter, not in the classification: the caller
    still gets the ``InvalidRequestError``, and nothing records it as an engine
    fault either.

    A refusal is marked on the SPAN by CLASS NAME, via attributes, rather than
    through ``record_exception``. Two reasons, and the first is the sharper
    one: ``record_exception`` sets the span status description to
    ``str(exc)[:200]``, which is EXPORTED to the OTLP backend -- and a refusal
    message is precisely the one that reliably carries a caller-supplied path,
    URL or profile name (``har_path '/Users/…/private/creds.har' resolves
    outside …``). ``_record_engine_health`` and ``LAUNCH_FAILED`` both keep the
    class name and never the message for exactly that reason; sending the full
    string off-box here would apply that rule in reverse. Second, it would mark
    the span ERROR, so trace-based launch error-rate alerting inherits the
    misattribution the counter split just removed. A genuine engine failure
    still goes through ``record_exception`` unchanged.
    """
    with span("octowright.browser.launch", kind=kind_hint) as sp:
        try:
            yield sp
        except BaseException as exc:
            if isinstance(exc, InvalidRequestError):
                set_attrs(sp, refused="invalid_request", refusal_error=type(exc).__name__)
                LAUNCH_REFUSED.add(1, attributes={"reason": "invalid_request"})
            else:
                record_exception(sp, exc)
                LAUNCH_FAILED.add(1, attributes={"kind": kind_hint, "error": type(exc).__name__})
            raise
