# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Smoke tests for the ``octowright._tracing`` re-export shim.

The span + metric implementations now live in ``provide.telemetry`` (with
governance, lazy provider rebinding, and trace↔metric correlation) and are
covered exhaustively by that package's own test suite. Octowright only needs to
verify that the shim re-exports the expected callables and that basic usage
works through the ``octowright._tracing`` import path without a configured
provider (the no-op path).
"""

from __future__ import annotations

import provide.telemetry as pt

from octowright import _tracing


def test_shim_reexports_provide_telemetry_callables() -> None:
    """Each name octowright imports must be the provide.telemetry implementation."""
    assert _tracing.span is pt.span
    assert _tracing.set_attrs is pt.set_attrs
    assert _tracing.record_exception is pt.record_exception
    assert _tracing.counter is pt.counter
    assert _tracing.gauge is pt.gauge
    assert _tracing.histogram is pt.histogram
    assert _tracing._tracer is pt.get_tracer


def test_span_and_helpers_safe_without_provider() -> None:
    """span()/set_attrs()/record_exception() no-op cleanly when tracing is off."""
    from octowright._tracing import record_exception, set_attrs, span

    with span("octowright.smoke", kind="test") as sp:
        set_attrs(sp, extra=1, dropped=None)
        record_exception(sp, ValueError("noted"))


def test_counter_and_histogram_usable_through_shim() -> None:
    """counter()/histogram() return recorders whose add()/record() never raise."""
    from octowright._tracing import counter, histogram

    c = counter("octowright_shim_counter", description="t", unit="1")
    h = histogram("octowright_shim_hist", description="t", unit="s")
    c.add(1, attributes={"k": "v"})
    h.record(0.5, attributes={"k": "v"})


def test_gauge_usable_through_shim() -> None:
    """gauge() returns a recorder whose add() never raises."""
    from octowright._tracing import gauge

    g = gauge("octowright_shim_gauge", description="t", unit="1")
    g.add(5, attributes={"k": "v"})
